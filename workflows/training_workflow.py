import json

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from agents.quality_reviewer import create_quality_node
from agents.models import tools_for
from agents.planner import create_plan
from agents.specialists import create_specialist_node
from domain.limits import load_runtime_limits
from domain.state import WorkflowState

QUALITY_PLAN_STEP = 9
END_ROUTE = "__end__"


def analyze_requirements(state: WorkflowState) -> dict[str, object]:
    objective = state.get("objective") or ""
    messages = state.get("messages", [])
    if not objective:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                objective = str(message.content)
                break
            if isinstance(message, dict) and message.get("type") == "human":
                objective = str(message.get("content", ""))
                break
    return {
        "objective": objective,
        "status": "researching",
        "constraints": state.get("constraints", []),
        "plan": [step.__dict__ for step in create_plan(objective)],
    }


def require_training_package(state: WorkflowState) -> dict[str, object]:
    if state.get("status") == "failed":
        return {
            "status": "failed",
            "quality_feedback": "",
            "errors": state.get("errors", []),
        }
    for message in reversed(state.get("messages", [])):
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = json.loads(str(message.content))
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("valid") is True:
            plan = state.get("plan", [])
            completed = [
                step["order"]
                for step in plan
                if step.get("order", 0) < QUALITY_PLAN_STEP
            ]
            return {
                "status": "producing",
                "quality_feedback": "",
                "completed_plan_steps": completed,
                "current_plan_step": QUALITY_PLAN_STEP if plan else 0,
                "plan": [
                    {
                        **step,
                        "status": (
                            "done"
                            if step.get("order", 0) in completed
                            else (
                                "in_progress"
                                if step.get("order") == QUALITY_PLAN_STEP
                                else step.get("status", "pending")
                            )
                        ),
                    }
                    for step in plan
                ],
            }
    return {
        "status": "reviewing",
        "quality_feedback": (
            "Antes de gerar os ficheiros, cria um TrainingPackage JSON completo e valida-o "
            "com validar_pacote_formacao."
        ),
    }


def build_training_workflow():
    builder = StateGraph(WorkflowState)
    builder.add_node("requisitos", analyze_requirements)
    builder.add_node("formador", create_specialist_node("formador"))
    builder.add_node("validar_pacote", require_training_package)
    builder.add_node("tools_formador", ToolNode(tools_for("formador")))
    builder.add_node(
        "qualidade",
        create_quality_node(load_runtime_limits().max_quality_iterations),
    )
    builder.add_edge(START, "requisitos")
    builder.add_edge("requisitos", "formador")
    builder.add_conditional_edges(
        "formador",
        tools_condition,
        {"tools": "tools_formador", END_ROUTE: "validar_pacote"},
    )
    builder.add_edge("tools_formador", "formador")
    builder.add_conditional_edges(
        "validar_pacote",
        _route_after_package,
        {"formador": "formador", "qualidade": "qualidade", END_ROUTE: END},
    )
    builder.add_conditional_edges(
        "qualidade",
        _route_after_quality,
        {"formador": "formador", END_ROUTE: END},
    )
    return builder.compile()


def _route_after_package(state: WorkflowState) -> str:
    if state.get("status") == "failed":
        return END_ROUTE
    return "formador" if state.get("quality_feedback") else "qualidade"


def _route_after_quality(state: WorkflowState) -> str:
    if state.get("status") == "failed":
        return END_ROUTE
    if (
        state.get("quality_feedback")
        and state.get("quality_iterations", 0)
        < load_runtime_limits().max_quality_iterations
    ):
        return "formador"
    return END_ROUTE
