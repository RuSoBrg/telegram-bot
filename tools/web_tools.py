import json
import ipaddress
import os
import socket
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)
from typing import Any

from langchain_core.tools import tool
from domain.research_cache import ResearchCache
from tools.safe_execution import safe_tool_execution


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
MAX_SCRAPED_CONTENT = 12000
_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "metadata.google.internal"}


def _validate_public_url(url: str) -> str:
    """Validate a URL before any network request, including redirects."""
    parsed = urlparse(url.strip())
    hostname = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError(
            "O URL deve usar o esquema http ou https, conter um hostname "
            "e não ter credenciais."
        )

    normalized_hostname = hostname.rstrip(".").lower()
    if normalized_hostname in _BLOCKED_HOSTNAMES:
        raise ValueError("O hostname solicitado não é permitido.")
    try:
        addresses = {
            ipaddress.ip_address(result[4][0])
            for result in socket.getaddrinfo(
                normalized_hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except (OSError, ValueError) as error:
        raise ValueError("Não foi possível resolver o hostname solicitado.") from error
    if not addresses or any(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        for address in addresses
    ):
        raise ValueError("O hostname solicitado resolve para uma rede não pública.")
    return url.strip()


class _PublicRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file,
        code: int,
        msg: str,
        headers,
        newurl: str,
    ) -> Request | None:
        target = urljoin(request.full_url, newurl)
        _validate_public_url(target)
        return super().redirect_request(request, file, code, msg, headers, target)


@tool
@safe_tool_execution
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
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "reliability": "primary"
            if perfil in {"official_docs", "legislation", "technical"}
            else "unknown",
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


@tool
@safe_tool_execution
def raspar_pagina_web(url: str, timeout_seconds: int = 10) -> str:
    """Obtém uma página HTTP e converte o conteúdo principal para Markdown limpo.

    Args:
        url: URL HTTP ou HTTPS da página a extrair. Recursos locais, ficheiros
            ``file://`` e outros esquemas são rejeitados.
        timeout_seconds: Timeout da ligação e leitura, entre 1 e 30 segundos.

    Returns:
        JSON com URL final, título e conteúdo Markdown truncado a 12 000
        caracteres. Scripts, estilos, navegação e elementos não informativos
        são removidos antes da conversão.

    Raises:
        ValueError: Se o URL ou timeout forem inválidos.
        TimeoutError: Se o servidor não responder dentro do limite.
        RuntimeError: Se a página não puder ser descarregada.
    """
    if not 1 <= timeout_seconds <= 30:
        raise ValueError("timeout_seconds deve estar entre 1 e 30.")
    safe_url = _validate_public_url(url)

    request = Request(
        safe_url,
        headers={"User-Agent": "AI-Workspace/1.0 (+web-content-reader)"},
    )
    try:
        opener = build_opener(_PublicRedirectHandler)
        with opener.open(request, timeout=timeout_seconds) as response:
            html = response.read(2_000_000).decode(
                response.headers.get_content_charset() or "utf-8",
                errors="replace",
            )
            final_url = response.geturl()
    except TimeoutError as error:
        raise TimeoutError(f"Timeout ao obter {safe_url}") from error
    except (HTTPError, URLError, OSError) as error:
        raise RuntimeError(f"Falha ao obter {safe_url}: {error}") from error

    from bs4 import BeautifulSoup
    from markdownify import markdownify

    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        element.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    content = markdownify(str(main), heading_style="ATX").strip()
    content = "\n".join(line.rstrip() for line in content.splitlines())
    return json.dumps(
        {
            "url": final_url,
            "titulo": soup.title.get_text(" ", strip=True) if soup.title else "",
            "conteudo_markdown": content[:MAX_SCRAPED_CONTENT],
            "truncado": len(content) > MAX_SCRAPED_CONTENT,
        },
        ensure_ascii=False,
    )
