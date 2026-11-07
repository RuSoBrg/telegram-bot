import os
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from agents.models import tools_for
from agents.quality_reviewer import create_quality_node
from agents.specialists import create_specialist_node
from agents.supervisor import create_supervisor_node
from domain.db import create_async_checkpoint_saver, create_checkpoint_saver
from domain.limits import load_runtime_limits
from domain.models import WorkflowStatus
from domain.db import QuotaExceededError, UserQuotaStore
from domain.state import WorkflowState
from workflows.training_workflow import build_training_workflow


AGENTS = ("marketing", "engenharia", "vida", "formador", "generalista")
TOOLS_REQUIRING_APPROVAL = {"gerar_documento_excel"}
MAX_GRAPH_STEPS = 5
GRAPH_RECURSION_LIMIT = 30


def _interrupt_before_nodes() -> list[str]:
    if os.getenv("REQUIRE_HUMAN_APPROVAL", "true").lower() == "true":
        return ["tools_engenharia"]
    return []


def build_workflow(
    checkpointer: BaseCheckpointSaver | AsyncPostgresSaver | None = None,
) -> Any:
    """Build the production graph with durable PostgreSQL checkpointing."""
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
            "generalista",
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
                create_specialist_node(
                    agent,
                    "tools_engenharia" if agent == "engenharia" else tools_target,
                    f"qualidade_{agent}",
                    approval_node="approval_engenharia" if agent == "engenharia" else None,
                    approval_tools=TOOLS_REQUIRING_APPROVAL,
                ),
                destinations=(
                    "approval_engenharia" if agent == "engenharia" else tools_target,
                    f"qualidade_{agent}",
                    "quality_reviewer",
                ),
            )
            tool_node = ToolNode(tools_for(agent))
            if agent == "engenharia":
                builder.add_node(
                    "approval_engenharia",
                    _request_engineering_approval,
                    destinations=("tools_engenharia",),
                )
                builder.add_edge("approval_engenharia", "tools_engenharia")
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
                    approved_target="quality_reviewer",
                ),
                destinations=(agent,),
            )

    builder.add_edge(START, "enforce_quota")
    builder.add_edge("enforce_quota", "supervisor")
    builder.add_edge("training_workflow", "quality_reviewer")

    saver = checkpointer or create_checkpoint_saver()
    return builder.compile(checkpointer=saver)


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
        if state.get("approval_decision") == "rejected":
            from langchain_core.messages import ToolMessage

            pending = [
                message for message in state.get("messages", [])
                if getattr(message, "tool_calls", None)
            ]
            return Command(
                goto=target,
                update={
                    "messages": [
                        ToolMessage(
                            content="Rejeitado pelo utilizador",
                            tool_call_id=call["id"],
                        )
                        for message in pending
                        for call in message.tool_calls
                    ],
                    "approval_decision": "",
                    "approval_required": False,
                    "steps": steps + 1,
                },
            )
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
    from langgraph.types import interrupt

    decision = interrupt(
        {
            "type": "engineering_approval",
            "message": "É necessária aprovação para executar esta ferramenta.",
        }
    )
    return {
        "status": WorkflowStatus.PRODUCING.value,
        "approval_required": False,
        "approval_decision": decision,
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
    return {
        "status": "producing",
        "iteration": 0,
        "quality_iterations": 0,
        "steps": 0,
        "errors": [],
        "research_sources": [],
        "artifacts": [],
        "quality_report": None,
        "quality_feedback": "",
        "approval_required": False,
        "approval_decision": "",
    }
