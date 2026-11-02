from langchain_core.messages import AIMessage, SystemMessage
from langgraph.types import Command

from agents.models import supervisor_model
from agents.runtime import PLAFOND_EXAUSTO_MESSAGE, load_prompt, invoke_with_fallback
from domain.state import WorkflowState
from domain.limits import load_runtime_limits


def create_supervisor_node():
    """Create the LangGraph node that routes each request to an agent."""
    model = supervisor_model()
    max_steps = load_runtime_limits().max_steps

    def supervisor_node(state: WorkflowState):
        steps = state.get("steps", 0)
        if steps >= max_steps:
            return Command(
                goto="quality_reviewer",
                update={
                    "steps": steps,
                    "status": "failed",
                    "errors": [
                        f"O workflow excedeu o limite de {max_steps} passos."
                    ],
                    "instruction": "",
                },
            )
        messages = [
            SystemMessage(
                content=load_prompt("supervisor.md")
                + "\n\nEscolhe entre marketing, engenharia, vida, formador ou finalizar."
            )
        ] + state.get("messages", [])
        try:
            decision = invoke_with_fallback(model, None, messages)
        except Exception as error:
            detail = str(error)
            return Command(
                goto="__end__",
                update={
                    "status": "failed",
                    "steps": steps + 1,
                    "errors": [detail],
                    "messages": [
                        AIMessage(
                            content=(
                                PLAFOND_EXAUSTO_MESSAGE
                                if "plafond" in detail.lower()
                                else f"Não foi possível encaminhar o pedido: {detail}"
                            )
                        )
                    ],
                },
            )
        destination = {
            "formador": "training_workflow",
            "finalizar": "quality_reviewer",
        }.get(decision.destino, decision.destino)
        return Command(
            goto=destination,
            update={
                "instruction": decision.resposta_ou_instrucao,
                "next_agent": decision.destino,
                "steps": steps + 1,
            },
        )

    return supervisor_node
