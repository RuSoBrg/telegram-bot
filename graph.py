"""Ponto de compatibilidade e composição pública do grafo principal."""

__all__ = ["grafo", "build_workflow"]


def __getattr__(name: str):
    """Carrega o grafo apenas quando uma interface realmente o solicita."""

    if name == "grafo":
        from workflows.main_workflow import build_workflow

        return build_workflow()
    if name == "build_workflow":
        from workflows.main_workflow import build_workflow

        return build_workflow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
