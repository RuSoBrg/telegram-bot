from dataclasses import dataclass


@dataclass(frozen=True)
class ApprovalRequest:
    reason: str
    action: str
    artifact_count: int
    source_count: int
    estimated_cost: str = "desconhecido"

    def as_dict(self) -> dict:
        return {
            "tipo": "aprovacao_humana",
            "mensagem": "Aprovação necessária antes de continuar.",
            "motivo": self.reason,
            "ação": self.action,
            "ficheiros": self.artifact_count,
            "fontes_externas": self.source_count,
            "custo_estimado": self.estimated_cost,
        }


def requires_human_approval(
    *,
    target: str,
    artifacts: list[dict],
    sources: list[dict],
    objective: str,
) -> ApprovalRequest | None:
    text = objective.casefold()
    media = any(
        str(item.get("mime_type", "")).startswith(("video/", "audio/", "image/"))
        for item in artifacts
    )
    legal = any(term in text for term in ("legal", "legislação", "lei", "contrato"))
    extensive = len(sources) >= 8 or len(artifacts) >= 5
    if target == "formador" or media or legal or extensive:
        reason = "pacote de formação" if target == "formador" else "operação com impacto ou custo"
        return ApprovalRequest(
            reason=reason,
            action="gerar e enviar para Telegram",
            artifact_count=len(artifacts),
            source_count=len(sources),
        )
    return None
