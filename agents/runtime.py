import logging
from pathlib import Path
from typing import Any, Iterable

from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
PLAFOND_EXAUSTO_MESSAGE = (
    "Neste momento esgotei o meu plafond nos fornecedores principais. "
    "Retomarei o serviço a 100% no início do próximo ciclo. Por favor, "
    "tenta um pedido mais simples amanhã."
)


class ProviderExhaustedError(RuntimeError):
    """Raised when every configured provider route is unavailable."""


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


def prepare_messages_for_model(messages: list[Any]) -> list[Any]:
    """Ensure a model request starts a new user turn after an AI response."""
    prepared = list(messages)
    if not prepared or not isinstance(prepared[-1], AIMessage):
        return prepared
    if prepared[-1].tool_calls:
        raise ValueError(
            "Não é possível invocar o modelo enquanto existem chamadas de ferramenta "
            "sem os respetivos ToolMessages."
        )
    prepared.append(HumanMessage(content="Prossegue com o próximo passo da análise."))
    return prepared


def _is_api_transient_error(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    error_name = type(error).__name__.lower()
    authentication_error = (
        "authenticationerror" in error_name
        or "insufficientquota" in error_name
    )
    quota_error = any(
        marker in str(error).upper()
        for marker in (
            "INSUFFICIENT_QUOTA",
            "INSUFFICIENTQUOTA",
            "QUOTA_EXCEEDED",
            "BILLING",
            "CREDIT",
            "RESOURCE_EXHAUSTED",
        )
    )
    return authentication_error or quota_error or status_code in {429, 500, 502, 503, 504} or any(
        marker in str(error).upper()
        for marker in (
            "429",
            "500",
            "502",
            "503",
            "504",
            "UNAVAILABLE",
        )
    )


def _provider_chain(primary: Any, fallback: Any, fallbacks: Iterable[Any]) -> list[Any]:
    chain: list[Any] = []
    for model in (primary, fallback, *fallbacks):
        if model is not None and not any(model is existing for existing in chain):
            chain.append(model)
    return chain


def invoke_with_fallback(
    primary: Any,
    fallback: Any,
    messages: Any,
    *,
    fallbacks: Iterable[Any] = (),
) -> Any:
    """Invoke an ordered provider chain and never hide provider exhaustion."""
    failures: list[str] = []
    for model in _provider_chain(primary, fallback, fallbacks):
        try:
            return model.invoke(messages)
        except Exception as error:
            if not _is_api_transient_error(error):
                raise
            failures.append(f"{type(error).__name__}: {error}")
            logger.warning("Rota de modelo indisponível; a tentar a seguinte.")
    detail = "; ".join(failures) or "Nenhuma rota de modelo configurada."
    raise ProviderExhaustedError(f"{PLAFOND_EXAUSTO_MESSAGE} ({detail})")


async def ainvoke_with_fallback(
    primary: Any,
    fallback: Any,
    messages: Any,
    *,
    fallbacks: Iterable[Any] = (),
) -> Any:
    """Async equivalent of :func:`invoke_with_fallback`."""
    failures: list[str] = []
    for model in _provider_chain(primary, fallback, fallbacks):
        try:
            return await model.ainvoke(messages)
        except Exception as error:
            if not _is_api_transient_error(error):
                raise
            failures.append(f"{type(error).__name__}: {error}")
            logger.warning("Rota de modelo assíncrona indisponível; a tentar a seguinte.")
    detail = "; ".join(failures) or "Nenhuma rota de modelo configurada."
    raise ProviderExhaustedError(f"{PLAFOND_EXAUSTO_MESSAGE} ({detail})")
