from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode

from agents.models import tools_for
from agents.quality_reviewer import create_quality_node
from agents.specialists import create_specialist_node
from agents.supervisor import create_supervisor_node
from domain.checkpoint import create_checkpoint_saver
from domain.limits import load_runtime_limits
from domain.state import WorkflowState
from workflows.training_workflow import build_training_workflow


AGENTS = ("marketing", "engenharia", "vida", "formador")


def build_workflow():
    builder = StateGraph(WorkflowState)
    builder.add_node(
        "supervisor",
        create_supervisor_node(),
        destinations=(*AGENTS, "training_workflow", "__end__"),
    )
    builder.add_node("training_workflow", build_training_workflow())

    for agent in AGENTS:
        if agent != "formador":
            builder.add_node(
                agent,
                create_specialist_node(agent, f"tools_{agent}", f"qualidade_{agent}"),
                destinations=(f"tools_{agent}", f"qualidade_{agent}", "__end__"),
            )
            tool_node = ToolNode(tools_for(agent))
            builder.add_node(
                f"tools_{agent}",
                lambda state, node=tool_node, target=agent: _run_tools(
                    node, state, target
                ),
                destinations=(agent, "__end__"),
            )
            builder.add_node(
                f"qualidade_{agent}",
                create_quality_node(
                    load_runtime_limits().max_quality_iterations,
                    target=agent,
                ),
                destinations=(agent, "__end__"),
            )

    builder.add_edge(START, "supervisor")
    return builder.compile(checkpointer=create_checkpoint_saver())


def _run_tools(node: ToolNode, state: WorkflowState, target: str):
    from langgraph.types import Command

    return Command(goto=target, update=node.invoke(state))


grafo = build_workflow()
