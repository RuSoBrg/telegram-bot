import json
import os

from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.types import Command, interrupt

from agents.models import quality_models, quality_tools
from agents.runtime import invoke_with_fallback, load_prompt
from domain.models import ReviewResult
from domain.quality import build_review_result
from domain.state import WorkflowState
from tools.validation_tools import validar_artefactos
from domain.approval import requires_human_approval
from agents.planner import mark_steps_done


QUALITY_PROMPTS = {
    "formador": "qualidade_formador.md",
    "engenharia": "qualidade_tecnica.md",
    "marketing": "qualidade_editorial.md",
    "vida": "qualidade_editorial.md",
}
REVIEWER_ROLES = {
    "formador": ("pedagogical", "qualidade_formador.md"),
    "engenharia": ("technical", "qualidade_tecnica.md"),
    "marketing": ("editorial", "qualidade_editorial.md"),
    "vida": ("operational", "qualidade_editorial.md"),
}


def _observations(messages):
    artifacts, sources = [], []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = json.loads(str(message.content))
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("tipo") == "ficheiro_gerado" and payload.get("uri"):
            artifacts.append(payload)
        artifacts.extend(payload.get("artefacts", []))
        sources.extend(payload.get("fontes", []))
    return artifacts, sources


def create_quality_node(max_iterations: int, target: str = "formador"):
    reviewer, reviewer_fallback, research, research_fallback = quality_models()

    def quality_node(state: WorkflowState):
        iteration = state.get("quality_iterations", 0)
        if iteration >= max_iterations:
            detail = (
                f"A revisão excedeu o limite de {max_iterations} iterações."
            )
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        ToolMessage(content=f"❌ {detail}", tool_call_id="quality-limit")
                    ],
                    "quality_feedback": "",
                    "status": "failed",
                    "quality_iterations": iteration,
                    "errors": [detail],
                },
            )

        artifacts, sources = _observations(state.get("messages", []))
        validation = (
            json.loads(
                validar_artefactos.invoke(
                    {
                        "paths": [
                            item.get("key", item["uri"])
                            for item in artifacts
                            if item.get("uri")
                        ]
                    }
                )
            )
            if artifacts
            else {}
        )
        prompt = load_prompt("qualidade.md") + "\n\n" + load_prompt(
            QUALITY_PROMPTS.get(target, "qualidade.md")
        )
        prompt += (
            f"\n\nAgente: {target}. Iteração: {iteration + 1}."
            "\nVerifica factos atuais com Tavily e ficheiros com validar_artefactos."
        )
        messages = [SystemMessage(content=prompt)] + state["messages"]
        try:
            research_response = invoke_with_fallback(research, research_fallback, messages)
            if research_response.tool_calls:
                messages.append(research_response)
                for call in research_response.tool_calls:
                    tool = next(
                        candidate for candidate in quality_tools() if candidate.name == call["name"]
                    )
                    result = tool.invoke(call["args"])
                    messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
            reviewer_specs = [REVIEWER_ROLES.get(target, ("general", "qualidade.md"))]
            if target in {"formador", "engenharia", "marketing", "vida"}:
                reviewer_specs.append(("factual", "qualidade_factual.md"))
            reviewer_decisions = []
            for role, prompt_file in reviewer_specs:
                role_messages = [
                    SystemMessage(
                        content=prompt
                        + f"\n\nÉs o revisor {role}. Emite apenas o teu parecer estruturado."
                        + "\n\n"
                        + load_prompt(prompt_file)
                    )
                ] + messages[1:]
                reviewer_decisions.append(
                    invoke_with_fallback(reviewer, reviewer_fallback, role_messages)
                )
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            report = ReviewResult(
                approved=False,
                score=0,
                blocking_issues=[f"A revisão automática falhou: {detail}"],
            )
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        ToolMessage(
                            content=f"❌ A supervisão falhou e o workflow foi terminado. Erro: {detail}",
                            tool_call_id="quality-error",
                        )
                    ],
                    "quality_report": report.model_dump(mode="json"),
                    "quality_feedback": "",
                    "quality_iterations": iteration + 1,
                    "status": "failed",
                    "errors": [f"Falha na supervisão automática: {detail}"],
                },
            )

        blocking = [
            issue
            for decision in reviewer_decisions
            for issue in decision.blocking_issues
        ]
        blocking.extend(validation.get("errors", []))
        warnings = [
            warning
            for decision in reviewer_decisions
            for warning in decision.warnings
        ]
        required_changes = [
            change
            for decision in reviewer_decisions
            for change in decision.required_changes
        ]
        verified_sources = [
            source
            for decision in reviewer_decisions
            for source in decision.verified_sources
        ]
        score = min((decision.score for decision in reviewer_decisions), default=0)
        approved = all(decision.aprovado for decision in reviewer_decisions)
        feedback = "\n".join(
            decision.feedback for decision in reviewer_decisions if decision.feedback
        )
        report = build_review_result(
            approved=approved,
            score=score,
            blocking_issues=blocking,
            warnings=warnings,
            required_changes=required_changes,
            verified_sources=verified_sources,
        )
        result = {
            "quality_report": report.model_dump(mode="json"),
            "quality_iterations": iteration + 1,
            "iteration": iteration + 1,
            "research_sources": sources,
            "artifacts": artifacts,
            "approval_required": False,
        }
        if report.approved:
            if state.get("plan"):
                current = state.get("current_plan_step", 0)
                result["completed_plan_steps"] = [
                    *state.get("completed_plan_steps", []),
                    current,
                ]
                result["current_plan_step"] = min(current + 1, len(state["plan"]))
                result["plan"] = mark_steps_done(
                    state["plan"],
                    result["completed_plan_steps"],
                )
            approval_request = requires_human_approval(
                target=target,
                artifacts=artifacts,
                sources=sources,
                objective=state.get("objective", ""),
            )
            if approval_request and os.getenv("REQUIRE_HUMAN_APPROVAL", "true").lower() == "true":
                approval = interrupt({**approval_request.as_dict(), "score": report.score})
                if approval != "approved":
                    return Command(
                        goto=target,
                        update={
                            **result,
                            "status": "rejected",
                            "quality_feedback": "Pacote rejeitado.",
                        },
                    )
            return Command(
                goto="__end__",
                update={**result, "status": "approved", "quality_feedback": ""},
            )
        return Command(
            goto=target,
            update={
                **result,
                "status": "reviewing",
                "quality_feedback": feedback or "Corrige os problemas indicados na revisão.",
            },
        )

    return quality_node
