"""Ponto de compatibilidade e composição pública do grafo principal."""

from agents.runtime import content_to_text as _content_to_text
from agents.runtime import invoke_with_fallback as _invoke_google

__all__ = ["grafo", "_content_to_text", "_invoke_google"]


def __getattr__(name: str):
    """Carrega o grafo apenas quando uma interface realmente o solicita."""

    if name == "grafo":
        from workflows.main_workflow import grafo

        return grafo
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
