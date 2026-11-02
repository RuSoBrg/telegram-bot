from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from agents.models import tools_for
from agents.quality_reviewer import create_quality_node
from agents.specialists import create_specialist_node
from agents.supervisor import create_supervisor_node
from domain.checkpoint import (
    create_async_checkpoint_saver,
    create_checkpoint_saver,
    get_checkpoint_saver,
)
from domain.limits import load_runtime_limits
from domain.models import WorkflowStatus
from domain.quotas import QuotaExceededError, UserQuotaStore
from domain.state import WorkflowState
from workflows.training_workflow import build_training_workflow


AGENTS = ("marketing", "engenharia", "vida", "formador")
MAX_GRAPH_STEPS = 5
GRAPH_RECURSION_LIMIT = 5


def build_workflow(
    checkpointer: BaseCheckpointSaver | AsyncPostgresSaver | None = None,
) -> Any:
    """Build the resilient graph used by synchronous application entry points.

    The PostgreSQL saver is preferred, while ``get_checkpoint_saver`` keeps local
    development available when PostgreSQL is temporarily unavailable.
    """
    builder = StateGraph(WorkflowState)
    builder.add_node("enforce_quota", _enforce_quota)
    builder.add_node(
        "supervisor",
        create_supervisor_node(),
        destinations=(
            "marketing",
            "engenharia",
            "vida",
            "training_workflow",
            "quality_reviewer",
        ),
    )
    builder.add_node("training_workflow", build_training_workflow())

    quality_limits = load_runtime_limits().max_quality_iterations

    # Gatekeeper de qualidade para respostas antes do __end__
    builder.add_node(
        "quality_reviewer",
        create_quality_node(quality_limits, target="supervisor"),
        destinations=("supervisor", "__end__"),
    )

    for agent in AGENTS:
        if agent != "formador":
            tools_target = (
                "approval_engenharia" if agent == "engenharia" else f"tools_{agent}"
            )
            builder.add_node(
                agent,
                create_specialist_node(agent, tools_target, f"qualidade_{agent}"),
                destinations=(tools_target, f"qualidade_{agent}"),
            )
            tool_node = ToolNode(tools_for(agent))
            if agent == "engenharia":
                builder.add_node(
                    "approval_engenharia",
                    _request_engineering_approval,
                    destinations=("tools_engenharia",),
                )
            builder.add_node(
                f"tools_{agent}",
                lambda state, node=tool_node, target=agent: _run_tools(
                    node, state, target
                ),
                destinations=(agent, f"qualidade_{agent}", "quality_reviewer"),
            )
            builder.add_node(
                f"qualidade_{agent}",
                create_quality_node(
                    quality_limits,
                    target=agent,
                ),
                destinations=(agent,),
            )

    builder.add_edge(START, "enforce_quota")
    builder.add_edge("enforce_quota", "supervisor")

    saver = checkpointer or get_checkpoint_saver(fallback_to_memory=True)
    return builder.compile(
        checkpointer=saver,
        interrupt_before=["tools_engenharia"],
    )


async def build_async_workflow(
    checkpointer: BaseCheckpointSaver | AsyncPostgresSaver | None = None,
) -> Any:
    """Compila o grafo assíncrono com AsyncPostgresSaver para máxima resiliência."""
    saver = checkpointer or await create_async_checkpoint_saver()
    return build_workflow(checkpointer=saver)


def _run_tools(node: ToolNode, state: WorkflowState, target: str) -> Any:
    from langgraph.types import Command

    steps = state.get("steps", 0)
    if steps >= MAX_GRAPH_STEPS:
        return Command(
            goto="quality_reviewer",
            update={
                "steps": steps,
                "status": "failed",
                "errors": [
                    f"O workflow excedeu o limite de {MAX_GRAPH_STEPS} passos."
                ],
            },
        )
    try:
        update_data = node.invoke(state)
    except Exception as error:
        return Command(
            goto="quality_reviewer",
            update={
                "steps": steps + 1,
                "status": "failed",
                "errors": [f"Falha na ferramenta: {type(error).__name__}: {error}"],
            },
        )
    update_data["steps"] = steps + 1
    return Command(goto=target, update=update_data)


def _request_engineering_approval(state: WorkflowState) -> dict[str, Any]:
    """Persist the approval status before pausing before engineering tools."""
    return {
        "status": WorkflowStatus.APPROVAL_REQUIRED,
        "approval_required": True,
    }


def _enforce_quota(state: WorkflowState) -> Any:
    """Consume one workflow quota unit before any model or tool invocation."""
    user_id = state.get("user_id") or "default_user"
    limits = load_runtime_limits()
    try:
        UserQuotaStore(
            max_operations=limits.max_user_operations,
            window_seconds=limits.quota_window_seconds,
        ).consume(user_id, "workflow")
    except QuotaExceededError as error:
        from langgraph.types import Command

        return Command(
            goto="__end__",
            update={
                "status": "failed",
                "errors": [str(error)],
                "messages": [{"role": "assistant", "content": str(error)}],
            },
        )
    return {"status": "producing"}


grafo = build_workflow()
