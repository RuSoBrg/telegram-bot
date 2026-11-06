from langchain_core.messages import AIMessage, SystemMessage
from langgraph.types import Command

from agents.models import specialist_models
from agents.runtime import (
    content_to_text,
    load_prompt,
    prepare_messages_for_model,
)
from domain.limits import load_runtime_limits
from domain.observability import timed_event
from domain.state import WorkflowState


AGENT_LABELS = {
    "marketing": "🎬 [CMO]",
    "vida": "🏠 [COO Vida]",
    "engenharia": "💻 [CTO]",
    "formador": "🎓 [FORMADOR]",
    "generalista": "🧭 [GENERALISTA]",
}


def create_specialist_node(
    agent_name: str,
    tools_node_name: str,
    quality_node_name: str,
    *,
    approval_node: str | None = None,
    approval_tools: set[str] | None = None,
):
    """Create a specialist node with its dynamic role prompt and fallback model."""
    model = specialist_models(agent_name)
    base_label = AGENT_LABELS.get(agent_name, f"[{agent_name}]")
    max_steps = load_runtime_limits().max_steps

    def specialist_node(state: WorkflowState):
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
                    "messages": [
                        AIMessage(
                            content=f"❌ O workflow excedeu o limite de {max_steps} passos."
                        )
                    ],
                },
            )
        prompt = load_prompt(f"{agent_name}.md")
        instruction = state.get("instruction", "")
        feedback = state.get("quality_feedback", "")
        if instruction:
            prompt += f"\n\nInstrução do supervisor: {instruction}"
        if feedback:
            prompt += f"\n\nFeedback da revisão a corrigir:\n{feedback}"

        try:
            conversation = prepare_messages_for_model(
                list(state.get("messages", []))
            )
            with timed_event(
                "agent_invocation",
                request_id=state.get("request_id", ""),
                agent=agent_name,
                model=lambda: getattr(model, "last_model", "router"),
            ):
                response = model.invoke([SystemMessage(content=prompt)] + conversation)
            model_used = response.additional_kwargs.get("model_used", "local-fallback")
            final_label = f"{base_label} / {model_used}"
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            return Command(
                goto=quality_node_name,
                update={
                    "messages": [
                        AIMessage(
                            content=(
                                f"❌ {base_label} falhou e o workflow foi terminado."
                            )
                        )
                    ],
                    "errors": [f"Falha no agente {agent_name}: {detail}"],
                    "status": "failed",
                    "steps": steps + 1,
                },
            )

        if response.tool_calls:
            destination = tools_node_name
            if (
                approval_node
                and os.getenv("REQUIRE_HUMAN_APPROVAL", "true").lower() == "true"
                and any(
                call.get("name") in (approval_tools or set())
                for call in response.tool_calls
                )
            ):
                destination = approval_node
            return Command(
                goto=destination,
                update={
                    "messages": [response],
                    "status": "producing",
                    "steps": steps + 1,
                    "agent_used": agent_name,
                    "model_used": model_used,
                },
            )
        return Command(
            goto=quality_node_name,
            update={
                "messages": [
                    AIMessage(content=f"{final_label}:\n{content_to_text(response.content)}")
                ],
                "status": "reviewing",
                "steps": steps + 1,
                "agent_used": agent_name,
                "model_used": model_used,
            },
        )

    return specialist_node
import os
