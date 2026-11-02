import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
PLAFOND_EXAUSTO_MESSAGE = (
    "Fiquei sem plafond nos meus fornecedores principais de IA. "
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
