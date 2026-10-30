from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from agents.models import tools_for
from agents.quality_reviewer import create_quality_node
from agents.specialists import create_specialist_node
from agents.supervisor import create_supervisor_node
from domain.checkpoint import create_checkpoint_saver
from domain.limits import load_runtime_limits
from domain.state import WorkflowState
from workflows.training_workflow import build_training_workflow


AGENTS = ("marketing", "engenharia", "vida", "formador")


def _route_agent(state: WorkflowState) -> str:
    target = state.get("next_agent")
    return target if target in AGENTS else "__end__"


def _route_quality(state: WorkflowState) -> str:
    target = state.get("quality_target")
    if state.get("status") == "failed":
        return "__end__"
    if (
        state.get("quality_feedback")
        and state.get("quality_iterations", 0) < load_runtime_limits().max_quality_iterations
        and target in AGENTS
    ):
        return target
    return "__end__"


def build_workflow():
    builder = StateGraph(WorkflowState)
    builder.add_node("supervisor", create_supervisor_node())
    builder.add_node("qualidade", create_quality_node(load_runtime_limits().max_quality_iterations))
    builder.add_node("training_workflow", build_training_workflow())

    for agent in AGENTS:
        if agent != "formador":
            builder.add_node(agent, create_specialist_node(agent))
            builder.add_node(f"tools_{agent}", ToolNode(tools_for(agent)))

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        _route_agent,
        {**{agent: ("training_workflow" if agent == "formador" else agent) for agent in AGENTS}, "__end__": END},
    )
    for agent in AGENTS:
        if agent == "formador":
            continue
        builder.add_conditional_edges(
            agent,
            tools_condition,
            {"tools": f"tools_{agent}", "__end__": "qualidade"},
        )
        builder.add_edge(f"tools_{agent}", agent)
    builder.add_conditional_edges(
        "qualidade",
        _route_quality,
        {
            **{
                agent: ("training_workflow" if agent == "formador" else agent)
                for agent in AGENTS
            },
            "__end__": END,
        },
    )
    return builder.compile(checkpointer=create_checkpoint_saver())


grafo = build_workflow()
