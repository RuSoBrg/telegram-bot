from langchain_core.messages import AIMessage, SystemMessage
from langgraph.types import Command

from agents.models import supervisor_model
from agents.runtime import load_prompt
from domain.state import WorkflowState


def create_supervisor_node():
    router = supervisor_model()

    def supervisor_node(state: WorkflowState):
        prompt = load_prompt("supervisor.md") + (
            "\n\nEscolhe entre marketing, engenharia, vida, formador ou finalizar."
        )
        try:
            decision = router.invoke([SystemMessage(content=prompt)] + state["messages"])
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        AIMessage(
                            content=(
                                "❌ O supervisor falhou e o workflow foi terminado.\n"
                                f"Erro: {detail}"
                            )
                        )
                    ],
                    "status": "failed",
                    "errors": [f"Falha no routing do supervisor: {detail}"],
                },
            )
        if decision.destino == "finalizar":
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        AIMessage(
                            content=f"🧠 [Supervisor]:\n{decision.resposta_ou_instrucao}"
                        )
                    ],
                    "status": "completed",
                },
            )
        return Command(
            goto=(
                "training_workflow"
                if decision.destino == "formador"
                else decision.destino
            ),
            update={
                "instruction": decision.resposta_ou_instrucao,
                "quality_feedback": "",
                "quality_iterations": 0,
                "iteration": 0,
                "status": "producing",
                "approval_required": False,
                "errors": [],
            },
        )

    return supervisor_node
