import json
import logging
import os
from urllib.parse import urlparse
from typing import Any

from langchain_core.tools import tool
from domain.research_cache import ResearchCache


logger = logging.getLogger(__name__)
research_cache = ResearchCache(
    ttl_seconds=int(os.getenv("RESEARCH_CACHE_TTL_SECONDS", "3600")),
)
MAX_RESULTS = 5
MAX_CONTENT_PER_RESULT = 3000
RESEARCH_PROFILES = {
    "official_docs": ["docs.python.org", "docs.flutter.dev", "firebase.google.com", "learn.microsoft.com"],
    "legislation": ["gov.pt", "dre.pt", "iefp.pt", "seg-social.pt"],
    "pedagogy": ["dges.gov.pt", "iefp.pt", "unesco.org", "oecd.org"],
    "technical": ["docs.python.org", "docs.flutter.dev", "firebase.google.com", "learn.microsoft.com"],
    "market": [],
    "general": [],
}


@tool
def pesquisar_web(
    consulta: str,
    objetivo: str = "",
    incluir_dominios: list[str] | None = None,
    perfil: str = "general",
) -> str:
    """Pesquisa informação atual na internet com Tavily e devolve fontes verificáveis.
    Usa esta ferramenta para factos recentes, documentação, legislação, normas,
    tendências, concorrência, preços, versões de software e referências pedagógicas.
    Não uses para perguntas que possam ser respondidas apenas com o contexto fornecido."""
    try:
        if perfil not in RESEARCH_PROFILES:
            raise ValueError(f"Perfil de pesquisa inválido: {perfil}")
        api_key = os.getenv("TAVILY_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("TAVILY_API_KEY não está configurada.")
        if not consulta or not consulta.strip():
            raise ValueError("A consulta de pesquisa não pode estar vazia.")
        domains = incluir_dominios or RESEARCH_PROFILES[perfil] or None
        cache_key = research_cache.key(consulta, perfil, domains)
        cached = (
            research_cache.get(cache_key)
            if os.getenv("RESEARCH_CACHE_ENABLED", "true").lower() == "true"
            else None
        )
        if cached is not None:
            cached["cache"] = "hit"
            return json.dumps(cached, ensure_ascii=False)

        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        response: dict[str, Any] = client.search(
            query=consulta.strip(),
            search_depth="advanced",
            topic="general",
            max_results=int(os.getenv("MAX_RESEARCH_RESULTS", str(MAX_RESULTS))),
            include_answer=True,
            include_raw_content=False,
            include_domains=domains,
        )
        fontes = [
            {
                "titulo": item.get("title", ""),
                "url": item.get("url", ""),
                "conteudo": (item.get("content") or "")[:MAX_CONTENT_PER_RESULT],
                "domain": urlparse(item.get("url", "")).netloc,
                "retrieved_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
                "reliability": "primary" if perfil in {"official_docs", "legislation", "technical"} else "unknown",
            }
            for item in response.get("results", [])
            if item.get("url")
        ]
        if not fontes:
            return json.dumps(
                {"consulta": consulta, "resultado": "Não foram encontradas fontes."},
                ensure_ascii=False,
            )
        payload = {
            "consulta": consulta,
            "objetivo": objetivo,
            "perfil": perfil,
            "resumo": response.get("answer", ""),
            "fontes": fontes,
            "instrucao": "Cita as fontes relevantes e distingue factos verificados de inferências.",
            "cache": "miss",
        }
        if os.getenv("RESEARCH_CACHE_ENABLED", "true").lower() == "true":
            research_cache.put(cache_key, payload)
        return json.dumps(payload, ensure_ascii=False)
    except Exception as e:
        logger.exception("Falha na pesquisa Tavily")
        return json.dumps(
            {"consulta": consulta, "erro": f"Error executing tool: {str(e)}. Please adjust your parameters and try again."},
            ensure_ascii=False,
        )
