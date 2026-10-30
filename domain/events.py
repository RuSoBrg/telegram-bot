from dataclasses import dataclass, field
import json
from typing import Any, Iterable, Mapping

from langchain_core.messages import AIMessage, ToolMessage


@dataclass(frozen=True)
class AppEvent:
    type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _tool_event(state: dict[str, Any], message: ToolMessage) -> AppEvent:
    try:
        payload = json.loads(str(message.content))
    except (TypeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict) and payload.get("tipo") == "ficheiro_gerado":
        return AppEvent("artifact", payload.get("filename", ""), {**payload, "state": state})
    return AppEvent("status", state.get("status", "updated"), {"state": state})


def _state_events(state: dict[str, Any]) -> list[AppEvent]:
    result = []
    if "__interrupt__" in state:
        result.append(AppEvent("status", "approval_required", {"state": state}))
    if state.get("status") == "failed":
        result.append(
            AppEvent(
                "error",
                "\n".join(str(item) for item in state.get("errors", [])),
                {"state": state},
            )
        )
    messages = state.get("messages") or []
    if not messages:
        result.append(AppEvent("status", state.get("status", "updated"), {"state": state}))
        return result
    message = messages[-1]
    if isinstance(message, AIMessage) and message.content:
        result.append(AppEvent("message", str(message.content), {"state": state}))
    elif isinstance(message, ToolMessage):
        result.append(_tool_event(state, message))
    return result


def iter_app_events(events: Iterable[Mapping[str, Any]]) -> Iterable[AppEvent]:
    """Converte valores do LangGraph no DTO único consumido pelas interfaces."""
    for raw_event in events:
        if not isinstance(raw_event, Mapping):
            continue
        state = dict(raw_event)
        yield from _state_events(state)
