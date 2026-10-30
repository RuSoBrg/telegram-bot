import os

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.types import Command

from agents.models import specialist_models
from agents.runtime import content_to_text, invoke_with_fallback, load_prompt
from domain.observability import timed_event
from domain.state import WorkflowState


AGENT_LABELS = {
    "marketing": "🎬 [CMO / Gemini]",
    "vida": "🏠 [COO Vida / Gemini]",
    "engenharia": "💻 [CTO / Gemini]",
    "formador": "🎓 [FORMADOR / Gemini]",
}


def create_specialist_node(
    agent_name: str,
    tools_node_name: str,
    quality_node_name: str,
):
    """Create a specialist node with its dynamic role prompt and fallback model."""
    primary, fallback = specialist_models(agent_name)
    label = AGENT_LABELS.get(agent_name, f"[{agent_name} / Gemini]")

    def specialist_node(state: WorkflowState):
        prompt = load_prompt(f"{agent_name}.md")
        instruction = state.get("instruction", "")
        feedback = state.get("quality_feedback", "")
        if instruction:
            prompt += f"\n\nInstrução do supervisor: {instruction}"
        if feedback:
            prompt += f"\n\nFeedback da revisão a corrigir:\n{feedback}"

        try:
            with timed_event(
                "agent_invocation",
                request_id=state.get("request_id", ""),
                agent=agent_name,
                model=os.getenv("GEMINI_MODEL", "configured"),
            ):
                response = invoke_with_fallback(
                    primary,
                    fallback,
                    [SystemMessage(content=prompt)] + state.get("messages", []),
                )
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        AIMessage(
                            content=f"❌ {label} falhou e o workflow foi terminado.\nErro: {detail}"
                        )
                    ],
                    "errors": [f"Falha no agente {agent_name}: {detail}"],
                    "status": "failed",
                },
            )

        if response.tool_calls:
            return Command(
                goto=tools_node_name,
                update={"messages": [response], "status": "producing"},
            )
        return Command(
            goto=quality_node_name,
            update={
                "messages": [
                    AIMessage(content=f"{label}:\n{content_to_text(response.content)}")
                ],
                "status": "reviewing",
            },
        )

    return specialist_node
