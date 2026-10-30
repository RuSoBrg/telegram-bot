"""Superfície HTTP opcional para integrações futuras.

Não inicia servidor nem expõe endpoints por omissão; a aplicação principal continua
a ser o Telegram/Streamlit.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class HealthResponse:
    status: str
    service: str = "ai-workspace"


def health() -> HealthResponse:
    return HealthResponse(status="ok")
