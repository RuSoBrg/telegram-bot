from __future__ import annotations

import re
from typing import Any


KEYWORDS: dict[str, tuple[str, ...]] = {
    "engenharia": (
        "código", "codigo", "bug", "erro", "flutter", "dart", "c#", "python",
        "api", "sql", "deploy", "docker", "git", "debug", "arquitetura",
    ),
    "marketing": (
        "tiktok", "roastnrizz", "growth", "conteúdo", "conteudo", "anúncio",
        "anuncio", "seo", "redes sociais", "aquisição", "aquisicao",
    ),
    "formador": (
        "sessão", "sessao", "curso", "formação", "formacao", "avaliação",
        "avaliacao", "plano de sessão", "apresentação pedagógica",
        "ccp", "pedagóg", "pedagog",
    ),
    "vida": (
        "carreira", "finanças", "financas", "carro", "rotinas", "saúde",
        "saude", "burocracia", "veículo", "veiculo",
    ),
}


def classify_request(text: str) -> str:
    """Classifica localmente um pedido, devolvendo sempre um destino válido."""
    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    scores = {
        agent: sum(1 for keyword in keywords if keyword in normalized)
        for agent, keywords in KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] else "generalista"


def classify_messages(messages: list[Any]) -> str:
    parts: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            parts.append(str(message.get("content", "")))
        elif hasattr(message, "content"):
            parts.append(str(message.content))
        else:
            parts.append(str(message))
    text = " ".join(parts)
    return classify_request(text)
