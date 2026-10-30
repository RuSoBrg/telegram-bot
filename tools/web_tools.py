import json
import logging
import os
from typing import Any

from langchain_core.tools import tool
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    MissingAPIKeyError,
    TimeoutError as TavilyTimeoutError,
    UsageLimitExceededError,
)


logger = logging.getLogger(__name__)
MAX_RESULTS = 5
MAX_CONTENT_PER_RESULT = 3000


@tool
def pesquisar_web(
    consulta: str,
    objetivo: str = "",
    incluir_dominios: list[str] | None = None,
) -> str:
    """Pesquisa informação atual na internet com Tavily e devolve fontes verificáveis.
    Usa esta ferramenta para factos recentes, documentação, legislação, normas,
    tendências, concorrência, preços, versões de software e referências pedagógicas.
    Não uses para perguntas que possam ser respondidas apenas com o contexto fornecido."""
    try:
        api_key = os.getenv("TAVILY_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("TAVILY_API_KEY não está configurada.")
        if not consulta or not consulta.strip():
            raise ValueError("A consulta de pesquisa não pode estar vazia.")

        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        response: dict[str, Any] = client.search(
            query=consulta.strip(),
            search_depth="advanced",
            topic="general",
            max_results=MAX_RESULTS,
            include_answer=True,
            include_raw_content=False,
            include_domains=incluir_dominios or None,
        )
        fontes = [
            {
                "titulo": item.get("title", ""),
                "url": item.get("url", ""),
                "conteudo": (item.get("content") or "")[:MAX_CONTENT_PER_RESULT],
            }
            for item in response.get("results", [])
            if item.get("url")
        ]
        if not fontes:
            return json.dumps(
                {"consulta": consulta, "resultado": "Não foram encontradas fontes."},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "consulta": consulta,
                "objetivo": objetivo,
                "resumo": response.get("answer", ""),
                "fontes": fontes,
                "instrucao": "Cita as fontes relevantes e distingue factos verificados de inferências.",
            },
            ensure_ascii=False,
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        BadRequestError,
        ForbiddenError,
        InvalidAPIKeyError,
        MissingAPIKeyError,
        TavilyTimeoutError,
        UsageLimitExceededError,
    ) as error:
        logger.exception("Falha na pesquisa Tavily")
        return json.dumps(
            {"consulta": consulta, "erro": str(error)},
            ensure_ascii=False,
        )
