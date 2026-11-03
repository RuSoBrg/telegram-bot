"""LangChain model factories backed by the local LiteLLM Router."""

from __future__ import annotations

import os
import re
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_litellm import ChatLiteLLMRouter
from litellm import Router
from pydantic import BaseModel, Field

from domain.models import RequirementsContext
from tools.code_tools import ler_ficheiro_codigo, listar_ficheiros_projeto
from tools.data_tools import analisar_ficheiro_dados, executar_query_sql
from tools.file_tools import (
    empacotar_artefactos,
    gerar_apresentacao_powerpoint,
    gerar_documento_excel,
    gerar_documento_pdf,
    gerar_documento_word,
    gerar_fotografia,
    gerar_video,
)
from tools.training_tools import validar_pacote_formacao
from tools.validation_tools import validar_artefactos
from tools.web_tools import pesquisar_web, raspar_pagina_web

load_dotenv()

LITELLM_CONFIG_PATH = Path(__file__).resolve().parent.parent / "litellm_config.yaml"
_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")
logger = logging.getLogger(__name__)


class RoutingDecision(BaseModel):
    destino: Literal["marketing", "engenharia", "vida", "formador", "finalizar"] = Field(
        description="Escolhe o agente adequado ou finalizar."
    )
    resposta_ou_instrucao: str = Field(
        description="Resposta final ou instrução completa para o agente escolhido."
    )


class QualityDecision(BaseModel):
    aprovado: bool
    score: int = Field(default=0, ge=0, le=100)
    feedback: str = ""
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    verified_sources: list[dict] = Field(default_factory=list)


def _quality_first_deployment(deployments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the highest-capability healthy deployment.

    LiteLLM has no native quality-first strategy. The Router still supplies
    this method with deployments that passed its availability, cooldown, and
    health filters, so sorting here keeps fallback behavior intact.
    """
    if not deployments:
        return None
    return max(
        deployments,
        key=lambda deployment: int(
            deployment.get("model_info", {}).get("quality_score", 0)
        ),
    )


class QualityFirstRouter(Router):
    """LiteLLM Router variant that prefers capability over latency."""

    def deployment_callback_on_failure(
        self,
        kwargs: dict[str, Any],
        completion_response: Any,
        start_time: Any,
        end_time: Any,
    ) -> bool:
        """Log the next quality-ranked deployment before LiteLLM fails over."""
        params = kwargs.get("litellm_params", {})
        failed_model = str(params.get("model", "unknown"))
        model_group = str(
            kwargs.get("model")
            or params.get("model_name")
            or kwargs.get("model_group")
            or "unknown"
        )
        deployments = self.get_model_list(model_name=model_group) or []
        ordered = sorted(
            deployments,
            key=lambda deployment: int(
                deployment.get("model_info", {}).get("order", 999)
            ),
        )
        failed_id = (kwargs.get("model_info") or {}).get("id")
        next_model = next(
            (
                str(candidate.get("litellm_params", {}).get("model", "unknown"))
                for candidate in ordered
                if (candidate.get("model_info") or {}).get("id") != failed_id
            ),
            None,
        )
        if next_model:
            logger.warning(
                "Falha no provedor principal %s. A iniciar fallback para %s.",
                failed_model,
                next_model,
            )
        else:
            logger.error(
                "Falha no provedor %s e não existem deployments alternativos "
                "configurados para %s.",
                failed_model,
                model_group,
            )
        return super().deployment_callback_on_failure(
            kwargs, completion_response, start_time, end_time
        )

    def _select_deployment_sync(
        self,
        *,
        strategy: str | None,
        selector: Any | None,
        model: str,
        healthy_deployments: list,
        messages: list[dict[str, str]] | None,
        input: str | list | None,
        request_kwargs: dict | None,
    ) -> Any | None:
        return _quality_first_deployment(healthy_deployments)

    async def _select_deployment_async(
        self,
        *,
        strategy: str | None,
        selector: Any | None,
        model: str,
        healthy_deployments: list,
        messages: list[dict[str, str]] | None,
        input: str | list | None,
        request_kwargs: dict | None,
    ) -> Any | None:
        return _quality_first_deployment(healthy_deployments)


def tools_for(agent: str) -> list[BaseTool]:
    common_research = [pesquisar_web, raspar_pagina_web]
    if agent == "formador":
        return [
            *common_research,
            gerar_documento_word,
            gerar_documento_excel,
            gerar_documento_pdf,
            gerar_apresentacao_powerpoint,
            validar_artefactos,
            validar_pacote_formacao,
            empacotar_artefactos,
        ]
    if agent == "marketing":
        return [
            *common_research,
            gerar_fotografia,
            gerar_video,
            gerar_apresentacao_powerpoint,
            gerar_documento_excel,
            validar_artefactos,
        ]
    if agent == "engenharia":
        return [
            *common_research,
            analisar_ficheiro_dados,
            executar_query_sql,
            ler_ficheiro_codigo,
            listar_ficheiros_projeto,
            gerar_documento_excel,
            validar_artefactos,
        ]
    if agent == "vida":
        return [
            *common_research,
            gerar_documento_word,
            gerar_documento_excel,
            gerar_documento_pdf,
            validar_artefactos,
        ]
    return common_research


def _expand_environment(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            return os.getenv(match.group(1), match.group(2) or "")

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_environment(item) for key, item in value.items()}
    return value


def _configured_deployments(model_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only deployments whose referenced credentials exist at runtime."""
    configured: list[dict[str, Any]] = []
    for deployment in model_list:
        params = deployment.get("litellm_params", {})
        api_key = params.get("api_key", "")
        if isinstance(api_key, str) and api_key.startswith("os.environ/"):
            variable = api_key.removeprefix("os.environ/")
            if not os.getenv(variable, "").strip():
                continue
        provider_model = str(params.get("model", ""))
        if provider_model.startswith("azure/") and not str(
            params.get("api_base", "")
        ).strip():
            continue
        configured.append(deployment)
    if not configured:
        raise RuntimeError("Nenhum deployment LiteLLM tem credenciais configuradas.")
    return configured


@lru_cache(maxsize=1)
def _router() -> Router:
    """Load the repository's LiteLLM routes once per process."""
    with LITELLM_CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        config = _expand_environment(yaml.safe_load(config_file) or {})
    model_list = config.get("model_list")
    router_settings = config.get("router_settings", {})
    if not isinstance(model_list, list) or not model_list:
        raise RuntimeError("litellm_config.yaml não contém model_list válida.")
    if not isinstance(router_settings, dict):
        raise RuntimeError("litellm_config.yaml não contém router_settings válido.")

    supported_settings = {
        key: router_settings[key]
        for key in (
            "routing_strategy",
            "num_retries",
            "max_fallbacks",
            "retry_after",
            "timeout",
            "allowed_fails",
            "cooldown_time",
            "retry_policy",
            "context_window_fallbacks",
            "enable_health_check_routing",
            "health_check_staleness_threshold",
            "background_health_check_model_groups",
            "enable_weighted_failover",
        )
        if key in router_settings
    }
    return QualityFirstRouter(
        model_list=_configured_deployments(model_list),
        # A recognized strategy keeps LiteLLM's normal health/cooldown path
        # active; QualityFirstRouter replaces its deployment ordering.
        routing_strategy="least-busy",
        **supported_settings,
    )


def _model(
    model_name: str,
    *,
    temperature: float,
    tools: list[BaseTool] | None = None,
) -> ChatLiteLLMRouter:
    model = ChatLiteLLMRouter(
        router=_router(),
        kwargs={"temperature": temperature},
    )
    model.set_default_model(model_name)
    return model.bind_tools(tools) if tools else model


def specialist_models(agent: str) -> BaseChatModel:
    """Return one Router-backed model; LiteLLM owns all provider fallbacks."""
    temperatures = {"marketing": 0.7, "vida": 0.4, "engenharia": 0.2, "formador": 0.3}
    route = "programmer-model" if agent == "engenharia" else "marketing-model"
    return _model(route, temperature=temperatures[agent], tools=tools_for(agent))


def file_reader_model() -> BaseChatModel:
    """Return the long-context Gemini route with its YAML fallback chain."""
    return _model("file-reader-model", temperature=0.1)


def quality_models() -> tuple[BaseChatModel, BaseChatModel]:
    quality_tools = [pesquisar_web, validar_artefactos]
    reviewer = _model("supervisor-model", temperature=0.1).with_structured_output(
        QualityDecision
    )
    research = _model("supervisor-model", temperature=0.1, tools=quality_tools)
    return reviewer, research


def quality_tools() -> list[BaseTool]:
    return [pesquisar_web, validar_artefactos]


def supervisor_model() -> BaseChatModel:
    return _model("supervisor-model", temperature=0.1).with_structured_output(
        RoutingDecision
    )


def requirements_model() -> BaseChatModel:
    return _model("supervisor-model", temperature=0.0).with_structured_output(
        RequirementsContext
    )
