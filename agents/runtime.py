import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(filename: str) -> str:
    """Load a UTF-8 prompt from the repository's prompts directory."""
    path = (PROMPTS_DIR / filename).resolve()
    if PROMPTS_DIR.resolve() not in path.parents or path.suffix.lower() != ".md":
        raise ValueError(f"Nome de prompt inválido: {filename}")
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("Prompt não encontrado: %s", path)
        raise RuntimeError(f"Prompt obrigatório não encontrado: {filename}") from None
    except OSError as error:
        logger.exception("Falha ao ler prompt: %s", path)
        raise RuntimeError(f"Não foi possível carregar o prompt: {filename}") from error


def content_to_text(content: Any) -> str:
    """Extract plain text from strings, content blocks, mappings, and sequences."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        return "".join(content_to_text(value) for value in content.values())
    if isinstance(content, (list, tuple)):
        return "".join(content_to_text(item) for item in content)
    return str(content)


def _is_api_transient_error(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    return status_code in {429, 500, 502, 503, 504} or any(
        marker in str(error).upper()
        for marker in (
            "429",
            "500",
            "502",
            "503",
            "504",
            "UNAVAILABLE",
            "RESOURCE_EXHAUSTED",
        )
    )


def invoke_with_fallback(primary: Any, fallback: Any, messages: Any) -> Any:
    """Invoke the primary model and retry transient API failures with fallback."""
    try:
        return primary.invoke(messages)
    except Exception as error:
        if not _is_api_transient_error(error):
            raise
        logger.warning("Modelo primário indisponível; a usar o fallback.")
        return fallback.invoke(messages)
