import json

from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from agents.quality_reviewer import create_quality_node
from agents.models import tools_for
from agents.models import requirements_model
from agents.planner import create_plan
from agents.specialists import create_specialist_node
from domain.limits import load_runtime_limits
from domain.state import WorkflowState

QUALITY_PLAN_STEP = 9


def analyze_requirements(state: WorkflowState):
    messages = state.get("messages", [])
    objective = state.get("objective") or ""
    if not messages:
        raise ValueError("Não é possível extrair requisitos sem mensagens.")
    context = requirements_model().invoke(
        [
            SystemMessage(
                content=(
                    "Extrai o contexto do pedido do utilizador. "
                    "Devolve apenas os campos do esquema estruturado."
                )
            ),
            *messages,
        ]
    )
    objective = context.objective or objective
    return Command(
        goto="formador",
        update={
            "objective": objective,
            "audience": context.audience,
            "status": "researching",
            "constraints": context.constraints or state.get("constraints", []),
            "plan": [step.__dict__ for step in create_plan(objective)],
        },
    )


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
    builder.add_node("requisitos", analyze_requirements, destinations=("formador",))
    builder.add_node(
        "formador",
        create_specialist_node("formador", "tools_formador", "validar_pacote"),
        destinations=("tools_formador", "qualidade", "__end__"),
    )
    builder.add_node(
        "validar_pacote",
        lambda state: _package_command(require_training_package(state)),
        destinations=("formador", "qualidade", "__end__"),
    )
    tool_node = ToolNode(tools_for("formador"))
    builder.add_node(
        "tools_formador",
        lambda state: _run_training_tools(tool_node, state),
        destinations=("formador", "__end__"),
    )
    builder.add_node(
        "qualidade",
        create_quality_node(
            load_runtime_limits().max_quality_iterations,
            target="formador",
        ),
        destinations=("formador", "__end__"),
    )
    builder.add_edge(START, "requisitos")
    return builder.compile()


def _run_training_tools(node: ToolNode, state: WorkflowState):
    return Command(goto="formador", update=node.invoke(state))


def _package_command(state: WorkflowState):
    if state.get("status") == "failed":
        return Command(goto="__end__", update=state)
    return Command(
        goto="formador" if state.get("quality_feedback") else "qualidade",
        update=state,
    )
