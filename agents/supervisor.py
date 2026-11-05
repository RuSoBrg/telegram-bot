from langchain_core.messages import AIMessage, SystemMessage
from langgraph.types import Command

from agents.models import supervisor_model
from agents.runtime import load_prompt
from domain.state import WorkflowState
from domain.limits import load_runtime_limits
from routing.keyword_classifier import classify_messages


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
                + (
                    "\n\nEscolhe entre marketing, engenharia, vida, formador, "
                    "generalista ou finalizar."
                )
            )
        ] + state.get("messages", [])
        try:
            decision = model.invoke(messages)
        except Exception as error:
            destination = classify_messages(state.get("messages", []))
            decision = type(
                "KeywordDecision",
                (),
                {
                    "destino": destination,
                    "resposta_ou_instrucao": (
                        "Encaminha este pedido ao especialista adequado e responde "
                        "com uma solução prática em Português de Portugal."
                    ),
                },
            )()
            fallback_error = f"Fallback local do supervisor: {type(error).__name__}: {error}"
        else:
            fallback_error = ""
            if getattr(getattr(model, "model", None), "last_model", "") == "local-fallback":
                decision.destino = classify_messages(state.get("messages", []))
                decision.resposta_ou_instrucao = (
                    "Responde diretamente ao pedido com uma orientação prática."
                )
                fallback_error = "O supervisor LLM ficou indisponível; classificação local aplicada."
        if decision.destino not in {
            "marketing", "engenharia", "vida", "formador", "generalista", "finalizar"
        }:
            decision.destino = classify_messages(state.get("messages", []))
        if decision.destino == "finalizar" and not decision.resposta_ou_instrucao.strip():
            decision.destino = "generalista"
        destination = {
            "formador": "training_workflow",
            "generalista": "generalista",
            "finalizar": "quality_reviewer",
        }.get(decision.destino, decision.destino)
        update = {
            "instruction": decision.resposta_ou_instrucao,
            "next_agent": decision.destino,
            "steps": steps + 1,
            "agent_used": "supervisor",
            "model_used": getattr(getattr(model, "model", None), "last_model", "router"),
        }
        if fallback_error:
            update["errors"] = [fallback_error]
        if decision.destino in {"generalista", "finalizar"}:
            update["messages"] = [
                AIMessage(content=decision.resposta_ou_instrucao)
            ]
        return Command(goto=destination, update=update)

    return supervisor_node
