from langchain_core.messages import SystemMessage
from langgraph.types import Command

from agents.models import supervisor_model
from agents.runtime import load_prompt
from domain.state import WorkflowState


def create_supervisor_node():
    """Create the LangGraph node that routes each request to an agent."""
    model = supervisor_model()

    def supervisor_node(state: WorkflowState):
        messages = [
            SystemMessage(
                content=load_prompt("supervisor.md")
                + "\n\nEscolhe entre marketing, engenharia, vida, formador ou finalizar."
            )
        ] + state.get("messages", [])
        decision = model.invoke(messages)
        destination = {
            "formador": "training_workflow",
            "finalizar": "__end__",
        }.get(decision.destino, decision.destino)
        return Command(
            goto=destination,
            update={
                "instruction": decision.resposta_ou_instrucao,
                "next_agent": decision.destino,
            },
        )

    return supervisor_node
