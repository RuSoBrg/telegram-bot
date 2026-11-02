"""Ponto de compatibilidade e composição pública do grafo principal."""

from agents.runtime import content_to_text as _content_to_text

__all__ = ["grafo", "_content_to_text"]


def __getattr__(name: str):
    """Carrega o grafo apenas quando uma interface realmente o solicita."""

    if name == "grafo":
        from workflows.main_workflow import build_workflow

        return build_workflow()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
