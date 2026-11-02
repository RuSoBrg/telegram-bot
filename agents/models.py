"""LangChain model factories backed by the local LiteLLM Router."""

from __future__ import annotations

import os
import re
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
from tools.web_tools import pesquisar_web

load_dotenv()

LITELLM_CONFIG_PATH = Path(__file__).resolve().parent.parent / "litellm_config.yaml"
_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")


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


def tools_for(agent: str) -> list[BaseTool]:
    common_research = [pesquisar_web]
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
            "retry_after",
            "timeout",
            "allowed_fails",
            "cooldown_time",
            "fallbacks",
            "context_window_fallbacks",
        )
        if key in router_settings
    }
    return Router(model_list=model_list, **supported_settings)


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
