from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping

from domain.events import AppEvent, iter_app_events
from workflows.main_workflow import GRAPH_RECURSION_LIMIT, build_async_workflow


@dataclass
class JobResult:
    text: str | None
    artifacts: list[dict]
    needs_approval: bool
    errors: list[str]
    state: dict[str, Any] = field(default_factory=dict)


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
        artifacts: list[dict] = []
        errors: list[str] = []
        needs_approval = False
        state: dict[str, Any] = {}
        async for raw_event in events:
            for event in iter_app_events([raw_event]):
                state = event.metadata.get("state", state)
                self._consume(event, state, artifacts, errors)
                if event.type == "message":
                    text = event.content
                elif event.type == "status" and event.content == "approval_required":
                    needs_approval = True
                elif event.type == "error" and event.content:
                    errors.append(event.content)
        return JobResult(
            text,
            artifacts,
            needs_approval,
            list(dict.fromkeys(errors)),
            state,
        )

    @staticmethod
    def _consume(
        event: AppEvent,
        state: Mapping[str, Any],
        artifacts: list[dict],
        errors: list[str],
    ) -> None:
        if event.type == "artifact":
            artifacts.append(event.metadata)
        elif event.type == "error" and event.content and event.content not in errors:
            errors.append(event.content)
        for error in state.get("errors", []):
            if str(error) not in errors:
                errors.append(str(error))


async def execute_workflow(
    graph: Any,
    graph_input: Any,
    thread_id: str,
) -> JobResult:
    return await WorkflowService(graph).execute(graph_input, thread_id)


async def run_job(
    prompt: str | Any,
    thread_id: str,
    *,
    user_id: str | None = None,
    graph: Any | None = None,
) -> JobResult:
    """Executa um pedido sem expor o grafo às interfaces."""
    workflow = graph or await build_async_workflow()
    graph_input = prompt if not isinstance(prompt, str) else {
        "messages": [{"role": "user", "content": prompt}],
        "request_id": thread_id,
        "user_id": user_id or thread_id,
    }
    return await WorkflowService(workflow).execute(graph_input, thread_id)
