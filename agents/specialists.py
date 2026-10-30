from langchain_core.messages import AIMessage, SystemMessage
from langgraph.types import Command

from domain.state import WorkflowState
from agents.models import specialist_models
from agents.runtime import content_to_text, invoke_with_fallback, load_prompt
from domain.observability import timed_event
import os


PROMPTS = {
    "marketing": ("marketing.md", "🎬 [CMO / Gemini]"),
    "vida": ("vida.md", "🏠 [COO Vida / Gemini]"),
    "engenharia": ("engenharia.md", "💻 [CTO / Gemini]"),
    "formador": ("formador.md", "🎓 [FORMADOR / Gemini]"),
}


def create_specialist_node(agent: str, next_node: str, quality_node: str):
    primary, fallback = specialist_models(agent)
    prompt_file, label = PROMPTS[agent]

    def specialist_node(state: WorkflowState):
        prompt = load_prompt(prompt_file)
        instruction = state.get("instruction", "")
        feedback = state.get("quality_feedback", "")
        if instruction:
            prompt += f"\n\nInstrução do CEO: {instruction}"
        if feedback:
            prompt += f"\n\nFeedback da supervisão a corrigir:\n{feedback}"
        plan = state.get("plan", [])
        current_step = state.get("current_plan_step")
        if plan and current_step:
            step = next(
                (item for item in plan if item.get("order") == current_step),
                None,
            )
            if step:
                prompt += (
                    f"\n\nPasso atual do planner ({current_step}): {step['title']}."
                    f"\nResultado esperado: {step['outcome']}."
                )
        try:
            with timed_event(
                "agent_invocation",
                request_id=state.get("request_id", ""),
                agent=agent,
                model=os.getenv("GEMINI_MODEL", "configured"),
            ):
                response = invoke_with_fallback(
                    primary,
                    fallback,
                    [SystemMessage(content=prompt)] + state["messages"],
                )
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        AIMessage(
                            content=(
                                f"❌ {label} falhou e o workflow foi terminado.\n"
                                f"Erro: {detail}"
                            )
                        )
                    ],
                    "errors": [f"Falha no agente {agent}: {detail}"],
                    "status": "failed",
                },
            )
        if response.tool_calls:
            return Command(
                goto=next_node,
                update={"messages": [response], "status": "producing"},
            )
        return Command(
            goto=quality_node,
            update={
                "messages": [
                    AIMessage(content=f"{label}:\n{content_to_text(response.content)}")
                ],
                "status": "reviewing",
            },
        )

    return specialist_node
