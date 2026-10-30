import os
from typing import Literal

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

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
from domain.models import RequirementsContext

load_dotenv()

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


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


def _gemini(model: str, temperature: float, tools: list[BaseTool]):
    return ChatGoogleGenerativeAI(
        model=model,
        temperature=temperature,
        max_retries=1,
    ).bind_tools(tools)


def specialist_models(agent: str):
    temperatures = {"marketing": 0.7, "vida": 0.4, "engenharia": 0.2, "formador": 0.3}
    primary = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    fallback = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
    tools = tools_for(agent)
    return (
        _gemini(primary, temperatures[agent], tools),
        ChatGoogleGenerativeAI(
            model=fallback,
            temperature=temperatures[agent],
            max_retries=2,
        ).bind_tools(tools),
    )


def quality_models():
    primary = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    fallback = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
    quality_tools = [pesquisar_web, validar_artefactos]
    return (
        ChatGoogleGenerativeAI(model=primary, temperature=0.1, max_retries=1).with_structured_output(
            QualityDecision
        ),
        ChatGoogleGenerativeAI(model=fallback, temperature=0.1, max_retries=2).with_structured_output(
            QualityDecision
        ),
        ChatGoogleGenerativeAI(model=primary, temperature=0.1, max_retries=1).bind_tools(
            quality_tools
        ),
        ChatGoogleGenerativeAI(model=fallback, temperature=0.1, max_retries=2).bind_tools(
            quality_tools
        ),
    )


def quality_tools() -> list[BaseTool]:
    return [pesquisar_web, validar_artefactos]


def supervisor_model():
    model = ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        temperature=0.1,
        max_retries=1,
    )
    return model.with_structured_output(RoutingDecision)


def requirements_model():
    return ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        temperature=0,
        max_retries=1,
    ).with_structured_output(RequirementsContext)
