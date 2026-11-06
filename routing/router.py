from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Sequence

import litellm
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages import ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from psycopg import Error as PsycopgError

from routing.availability import AvailabilityStore
from routing.catalog import ModelEntry, load_catalog
from routing.errors import ErrorKind, classify_error
from routing.alerts import notify_disabled

logger = logging.getLogger(__name__)


class ModelRouter:
    def __init__(
        self,
        catalog: dict[str, list[ModelEntry]] | None = None,
        availability: AvailabilityStore | None = None,
        budget_seconds: float | None = None,
    ):
        self.catalog = catalog or load_catalog()
        if availability is not None:
            self.availability = availability
        else:
            try:
                self.availability = AvailabilityStore()
            except (OSError, sqlite3.Error, PsycopgError):
                self.availability = AvailabilityStore(
                    location=os.getenv(
                        "MODEL_AVAILABILITY_DB_PATH",
                        "data/model_availability.db",
                    )
                )
        self.budget_seconds = budget_seconds or float(os.getenv("REQUEST_BUDGET_SECONDS", "90"))

    async def invoke(
        self,
        task: str,
        messages: Sequence[Any],
        tools: Sequence[Any] | None = None,
        schema: type[Any] | None = None,
    ) -> tuple[Any, str]:
        started = time.monotonic()
        required = set()
        if tools:
            required.add("tools")
        if schema:
            required.add("structured_output")
        candidates = sorted(
            [
                item for item in self.catalog.get(task, [])
                if required.issubset(item.capabilities)
            ],
            key=lambda item: item.score,
            reverse=True,
        )
        entries = [
            item for item in candidates
            if self._is_available(item.provider, item.model)
        ]
        entries = sorted(entries, key=lambda item: item.score, reverse=True)
        if not entries:
            blocked = []
            now = datetime.now(timezone.utc)
            snapshot = {
                (item["provider"], item["model"]): item
                for item in self.availability.snapshot()
            }
            for entry in candidates:
                status = snapshot.get((entry.provider, entry.model))
                if not status or status.get("reason") in {
                    ErrorKind.AUTH.value,
                    ErrorKind.MODEL_NOT_FOUND.value,
                }:
                    continue
                until = status.get("unavailable_until")
                if isinstance(until, str):
                    until = datetime.fromisoformat(until)
                if until and until.tzinfo is None:
                    until = until.replace(tzinfo=timezone.utc)
                if until and until > now:
                    blocked.append((until, entry))
            entries = [
                entry for _, entry in sorted(blocked, key=lambda pair: pair[0])[:2]
            ]
        if not entries:
            return self._fallback(messages, task), "local-fallback"
        last_error: Exception | None = None
        for entry in entries:
            if time.monotonic() - started >= self.budget_seconds:
                break
            try:
                result = await self._call(entry, messages, tools, schema)
                self._mark_success(entry)
                return result, entry.model_id
            except Exception as error:
                last_error = error
                classified = classify_error(
                    error,
                    fail_count=self._failure_count(
                        entry.provider, entry.model
                    ),
                    structured=schema is not None,
                )
                if classified.kind == ErrorKind.CONTEXT_TOO_LONG:
                    long_context = [
                        candidate
                        for candidate in entries
                        if "long_context" in candidate.capabilities
                    ]
                    if long_context:
                        entries = long_context
                if classified.kind in {
                    ErrorKind.CONTEXT_TOO_LONG,
                    ErrorKind.STRUCTURED_OUTPUT_FAILURE,
                }:
                    continue
                if classified.disable or classified.retry_at:
                    until = classified.retry_at or datetime.now(timezone.utc)
                    targets = (
                        [
                            candidate
                            for candidate in entries
                            if candidate.provider == entry.provider
                        ]
                        if classified.disable == "provider"
                        else [entry]
                    )
                    for target in targets:
                        self._mark_unavailable(
                            target,
                            until,
                            classified.kind.value,
                            str(error),
                        )
                    if classified.disable in {"provider", "model"}:
                        await notify_disabled(
                            classified.disable,
                            entry.provider,
                            entry.model,
                            classified.kind.value,
                        )
        if last_error:
            logger.warning("Todos os modelos falharam para %s: %s", task, last_error)
        return self._fallback(messages, task), "local-fallback"

    def _is_available(self, provider: str, model: str) -> bool:
        try:
            return self.availability.is_available(provider, model)
        except (OSError, sqlite3.Error, PsycopgError) as error:
            logger.warning("Falha ao consultar disponibilidade de %s/%s: %s", provider, model, error)
            return True

    def _failure_count(self, provider: str, model: str) -> int:
        try:
            return self.availability.failure_count(provider, model)
        except (OSError, sqlite3.Error, PsycopgError):
            return 0

    def _mark_unavailable(
        self, entry: ModelEntry, until: datetime, reason: str, error: str
    ) -> None:
        try:
            self.availability.mark_unavailable(
                entry.provider, entry.model, until, reason, error
            )
        except (OSError, sqlite3.Error, PsycopgError) as availability_error:
            logger.warning(
                "Falha ao guardar indisponibilidade de %s/%s: %s",
                entry.provider,
                entry.model,
                availability_error,
            )

    def _mark_success(self, entry: ModelEntry) -> None:
        try:
            self.availability.mark_success(entry.provider, entry.model)
        except (OSError, sqlite3.Error, PsycopgError) as error:
            logger.warning(
                "Falha ao limpar disponibilidade de %s/%s: %s",
                entry.provider,
                entry.model,
                error,
            )
    def invoke_sync(self, *args: Any, **kwargs: Any) -> tuple[Any, str]:
        return asyncio.run(self.invoke(*args, **kwargs))

    async def _call(self, entry: ModelEntry, messages, tools, schema):
        kwargs = self._kwargs(entry, messages, tools)
        if schema:
            try:
                response = await litellm.acompletion(
                    **kwargs, response_format=schema, timeout=entry.timeout_s
                )
                return self._parse_structured(response, schema)
            except Exception as error:
                classified = classify_error(error, structured=False)
                if classified.kind not in {
                    ErrorKind.STRUCTURED_OUTPUT_FAILURE,
                    ErrorKind.BAD_REQUEST,
                }:
                    raise
                fallback_messages = list(messages) + [{
                    "role": "user",
                    "content": "Responde apenas com JSON válido conforme o schema pedido.",
                }]
                response = await litellm.acompletion(
                    **self._kwargs(entry, fallback_messages, tools),
                    response_format={"type": "json_object"},
                    timeout=entry.timeout_s,
                )
                try:
                    content = self._content(response)
                    if isinstance(content, str):
                        content = json.loads(content)
                    return schema.model_validate(content)
                except Exception as parse_error:
                    raise parse_error from error
        response = await litellm.acompletion(**kwargs, timeout=entry.timeout_s)
        return self._message(response)

    @staticmethod
    def _kwargs(entry: ModelEntry, messages, tools) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": f"{entry.provider}/{entry.model}",
            "messages": _messages(messages),
        }
        if entry.api_key_env and not entry.base_url_env:
            params["api_key"] = os.getenv(entry.api_key_env)
        if tools:
            params["tools"] = [convert_to_openai_tool(tool) for tool in tools]
        if entry.api_base_env and os.getenv(entry.api_base_env):
            params["api_base"] = os.getenv(entry.api_base_env)
        if entry.api_version_env and os.getenv(entry.api_version_env):
            params["api_version"] = os.getenv(entry.api_version_env)
        if entry.base_url_env and os.getenv(entry.base_url_env):
            params["api_base"] = os.getenv(entry.base_url_env)
        return params

    @staticmethod
    def _parse_structured(response: Any, schema: type[Any]) -> Any:
        content = ModelRouter._content(response)
        if isinstance(content, schema):
            return content
        if isinstance(content, dict):
            return schema.model_validate(content)
        return schema.model_validate_json(str(content))

    @staticmethod
    def _content(response: Any) -> Any:
        message = response.choices[0].message
        return message.get("content") if isinstance(message, dict) else message.content

    @staticmethod
    def _message(response: Any) -> AIMessage:
        message = response.choices[0].message
        content = message.get("content", "") if isinstance(message, dict) else message.content
        tool_calls = message.get("tool_calls", []) if isinstance(message, dict) else getattr(message, "tool_calls", [])
        converted = []
        for call in tool_calls or []:
            if isinstance(call, dict):
                function = call.get("function", call)
                arguments = function.get("arguments", call.get("args", {}))
                name = function.get("name", call.get("name", ""))
                call_id = call.get("id", "")
            else:
                function = getattr(call, "function", None)
                arguments = getattr(function, "arguments", "{}")
                name = getattr(function, "name", "")
                call_id = getattr(call, "id", "")
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            converted.append(
                {"name": name, "args": arguments, "id": call_id, "type": "tool_call"}
            )
        return AIMessage(content=content or "", tool_calls=converted)

    @staticmethod
    def _fallback(messages: Sequence[Any], task: str, detail: str = "") -> str:
        request = "o teu pedido"
        for message in messages:
            if (
                isinstance(message, BaseMessage)
                and message.type == "human"
                and not message.additional_kwargs.get("synthetic")
            ):
                request = str(message.content)
            elif (
                isinstance(message, dict)
                and message.get("role") == "user"
                and not message.get("additional_kwargs", {}).get("synthetic")
            ):
                request = str(message.get("content", request))
        suffix = f" Estado: {detail}." if detail else ""
        return f"Não foi possível concluir «{request}» neste momento ({task}).{suffix}"


def _messages(messages: Sequence[Any]) -> list[dict[str, Any]]:
    result = []
    for message in messages:
        if isinstance(message, BaseMessage):
            role = (
                "assistant"
                if message.type == "ai"
                else "user"
                if message.type == "human"
                else "system"
                if message.type == "system"
                else "tool"
            )
            payload: dict[str, Any] = {"role": role, "content": _content_for_openai(message.content)}
            if isinstance(message, AIMessage) and message.tool_calls:
                payload["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["args"]),
                        },
                    }
                    for call in message.tool_calls
                ]
                payload.pop("content", None)
            if isinstance(message, ToolMessage):
                payload["tool_call_id"] = message.tool_call_id
            result.append(payload)
        else:
            result.append(dict(message))
    return result


def _content_for_openai(content: Any) -> Any:
    if isinstance(content, list):
        return [
            item if isinstance(item, dict) else {"type": "text", "text": str(item)}
            for item in content
        ]
    return content if isinstance(content, str) else str(content)
