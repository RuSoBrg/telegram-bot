from langchain_core.messages import AIMessage, SystemMessage

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
            return {
                "messages": [
                    AIMessage(
                        content=(
                            "❌ O supervisor falhou e o workflow foi terminado.\n"
                            f"Erro: {detail}"
                        )
                    )
                ],
                "next_agent": "finalizar",
                "status": "failed",
                "errors": [f"Falha no routing do supervisor: {detail}"],
            }
        if decision.destino == "finalizar":
            return {
                "messages": [AIMessage(content=f"🧠 [Supervisor]:\n{decision.resposta_ou_instrucao}")],
                "next_agent": "finalizar",
                "status": "completed",
            }
        return {
            "next_agent": decision.destino,
            "instruction": decision.resposta_ou_instrucao,
            "quality_target": decision.destino,
            "quality_feedback": "",
            "quality_iterations": 0,
            "iteration": 0,
            "status": "producing",
            "approval_required": False,
            "errors": [],
        }

    return supervisor_node
