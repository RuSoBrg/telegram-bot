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
from tools.training_tools import validar_pacote_formacao


def analyze_requirements(state: WorkflowState) -> dict:
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


def require_training_package(state: WorkflowState) -> dict:
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
                if step.get("order", 0) < 9
            ]
            return {
                "status": "producing",
                "quality_feedback": "",
                "completed_plan_steps": completed,
                "current_plan_step": 9 if plan else 0,
                "plan": [
                    {
                        **step,
                        "status": (
                            "done"
                            if step.get("order", 0) in completed
                            else "in_progress"
                            if step.get("order") == 9
                            else step.get("status", "pending")
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
        {"tools": "tools_formador", "__end__": "validar_pacote"},
    )
    builder.add_edge("tools_formador", "formador")
    builder.add_conditional_edges(
        "validar_pacote",
        lambda state: (
            "__end__"
            if state.get("status") == "failed"
            else ("formador" if state.get("quality_feedback") else "qualidade")
        ),
        {"formador": "formador", "qualidade": "qualidade", "__end__": END},
    )
    builder.add_conditional_edges(
        "qualidade",
        lambda state: (
            "__end__"
            if state.get("status") == "failed"
            else (
                "formador"
            if state.get("quality_feedback")
            and state.get("quality_iterations", 0)
            < load_runtime_limits().max_quality_iterations
            else "__end__")
        ),
        {"formador": "formador", "__end__": END},
    )
    return builder.compile()
