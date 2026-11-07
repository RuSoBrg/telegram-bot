import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

from langchain_core.messages import ToolMessage
from domain.events import AppEvent, iter_app_events
from workflows.main_workflow import GRAPH_RECURSION_LIMIT, build_async_workflow

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB


@dataclass
class JobResult:
    text: str | None
    artifacts: list[dict[str, Any]]
    needs_approval: bool
    errors: list[str]
    failed: bool
    state: dict[str, Any] = field(default_factory=dict)


def sanitize_filename(filename: str) -> str:
    cleaned = Path(filename).name.strip()
    sanitized = re.sub(r"[^a-zA-Z0-9._-]+", "_", cleaned)
    return sanitized or "ficheiro"


def is_allowed_artifact_path(path_str: str) -> bool:
    try:
        path = Path(path_str).resolve()
        if not path.is_file():
            return False
        if path.stat().st_size > MAX_FILE_BYTES:
            return False
        configured_roots = os.environ.get("AGENT_ALLOWED_ROOTS", "")
        allowed_roots = tuple(
            Path(r).expanduser().resolve()
            for r in configured_roots.split(os.pathsep)
            if r.strip()
        ) or (Path.cwd().resolve(), Path(__file__).resolve().parent.parent)
        return any(path == root or root in path.parents for root in allowed_roots)
    except Exception as exc:
        logger.warning("Falha ao validar caminho de artefacto %s: %s", path_str, exc)
        return False


def extract_artifacts_from_state(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extrai e valida todos os artefactos de todos os ToolMessages e do campo artifacts."""
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add_artifact(meta: dict[str, Any]) -> None:
        raw_name = meta.get("filename") or meta.get("name") or "ficheiro"
        filename = sanitize_filename(str(raw_name))
        item = dict(meta)
        item["filename"] = filename

        size = item.get("size_bytes", 0)
        if size and size > MAX_FILE_BYTES:
            logger.warning("Artefacto %s excede 50MB (%d bytes); ignorado.", filename, size)
            return

        uri = str(item.get("uri") or item.get("path") or "")
        if uri and not uri.startswith("db://"):
            if not is_allowed_artifact_path(uri):
                logger.warning("Caminho de artefacto inválido ou inacessível: %s", uri)
                return

        key = str(item.get("artifact_id") or item.get("sha256") or uri or filename)
        if key not in seen:
            seen.add(key)
            artifacts.append(item)

    # 1. Analisar todas as mensagens de ferramenta no histórico
    for message in state.get("messages", []):
        content = None
        if isinstance(message, ToolMessage):
            content = message.content
        elif isinstance(message, Mapping) and message.get("role") == "tool":
            content = message.get("content")

        if not content:
            continue

        try:
            payload = json.loads(str(content))
        except (TypeError, json.JSONDecodeError):
            continue

        if isinstance(payload, dict):
            if payload.get("tipo") == "ficheiro_gerado":
                _add_artifact(payload)
            for sub in payload.get("artefacts", []) or payload.get("artifacts", []):
                if isinstance(sub, dict):
                    _add_artifact(sub)

    # 2. Analisar artefactos guardados diretamente no estado
    for item in state.get("artifacts", []):
        if isinstance(item, dict):
            _add_artifact(item)

    return artifacts


class WorkflowService:
    """Camada assíncrona única para executar e normalizar resultados do workflow."""

    def __init__(self, graph: Any | None = None):
        self.graph = graph

    async def execute(
        self,
        graph_input: Any,
        thread_id: str,
        *,
        graph: Any | None = None,
    ) -> JobResult:
        workflow = graph or self.graph
        if workflow is None:
            raise RuntimeError("O grafo ainda não foi inicializado.")
        events = workflow.astream(
            graph_input,
            config={
                "configurable": {"thread_id": thread_id},
                "recursion_limit": GRAPH_RECURSION_LIMIT,
            },
            stream_mode="values",
        )
        return await self.collect(events)

    async def collect(self, events: AsyncIterator[Any]) -> JobResult:
        text: str | None = None
        errors: list[str] = []
        needs_approval = False
        state: dict[str, Any] = {}
        async for raw_event in events:
            for event in iter_app_events([raw_event]):
                state = event.metadata.get("state", state)
                if event.type == "message":
                    text = event.content
                elif event.type == "status" and event.content == "approval_required":
                    needs_approval = True
                elif event.type == "error" and event.content and event.content not in errors:
                    errors.append(event.content)
            for err in state.get("errors", []):
                if str(err) not in errors:
                    errors.append(str(err))

        artifacts = extract_artifacts_from_state(state)
        failed = state.get("status") == "failed" or bool(errors)
        return JobResult(
            text=text,
            artifacts=artifacts,
            needs_approval=needs_approval,
            errors=list(dict.fromkeys(errors)),
            failed=failed,
            state=state,
        )


async def execute_workflow(
    graph: Any,
    graph_input: Any,
    thread_id: str,
) -> JobResult:
    return await WorkflowService(graph).execute(graph_input, thread_id)


async def run_job(
    graph: Any,
    payload: Any = None,
    thread_id: str | None = None,
    *,
    user_id: str | None = None,
) -> JobResult:
    """Executa um trabalho LangGraph e devolve JobResult padronizado."""
    if isinstance(payload, str) and thread_id is None:
        thread_id = payload
        payload = graph
        graph = None
    elif thread_id is None and isinstance(graph, (dict, str)):
        thread_id = str(payload or "")
        payload = graph
        graph = None

    workflow = graph or await build_async_workflow()
    actual_thread = thread_id or "default"

    if isinstance(payload, str):
        graph_input: Any = {
            "messages": [{"role": "user", "content": payload}],
            "request_id": actual_thread,
            "user_id": user_id or actual_thread,
        }
    else:
        graph_input = payload

    return await WorkflowService(workflow).execute(graph_input, actual_thread)
