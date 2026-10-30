import logging
from pathlib import Path

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent


def load_prompt(filename: str) -> str:
    path = BASE_DIR / "prompts" / filename
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("Prompt não encontrado: %s", path)
        raise RuntimeError(f"Prompt obrigatório não encontrado: {filename}") from None
    except OSError:
        logger.exception("Falha ao ler prompt: %s", path)
        raise RuntimeError(f"Não foi possível carregar o prompt: {filename}") from None


def invoke_with_fallback(primary, fallback, messages):
    try:
        return primary.invoke(messages)
    except Exception as error:
        status_code = getattr(error, "status_code", None)
        transient = status_code in {429, 500, 502, 503, 504} or any(
            marker in str(error).upper()
            for marker in ("429", "500", "502", "503", "504", "UNAVAILABLE", "RESOURCE_EXHAUSTED")
        )
        if not transient:
            raise
        logger.warning("Modelo primário indisponível; a usar fallback")
        return fallback.invoke(messages)


def content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)
