from dataclasses import dataclass


@dataclass(frozen=True)
class PlanStep:
    order: int
    title: str
    outcome: str


def plan_as_state(steps: list[PlanStep], current: int = 1) -> list[dict]:
    return [
        {
            "order": step.order,
            "title": step.title,
            "outcome": step.outcome,
            "status": (
                "done"
                if step.order < current
                else "in_progress"
                if step.order == current
                else "pending"
            ),
        }
        for step in steps
    ]


def mark_steps_done(plan: list[dict], orders: list[int]) -> list[dict]:
    completed = set(orders)
    return [
        {**step, "status": "done" if step["order"] in completed else step["status"]}
        for step in plan
    ]


TRAINING_PLAN = (
    "Definir público e pré-requisitos",
    "Criar currículo",
    "Criar plano de cada sessão",
    "Criar apresentações",
    "Criar exercícios",
    "Criar soluções",
    "Criar avaliações",
    "Pesquisar referências",
    "Rever qualidade",
    "Empacotar e enviar",
)


def create_plan(request: str) -> list[PlanStep]:
    """Decompõe pedidos de formação complexos em passos auditáveis."""
    normalized = request.casefold()
    is_training = any(
        term in normalized
        for term in ("curso", "formação", "formacao", "sessão", "sessao", "formador")
    )
    if not is_training:
        return [PlanStep(1, "Executar pedido", "Resposta ou artefacto final")]
    return [
        PlanStep(index, title, title)
        for index, title in enumerate(TRAINING_PLAN, start=1)
    ]
