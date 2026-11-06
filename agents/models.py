"""Modelos LangChain adaptados ao router próprio com fallback persistente."""

from __future__ import annotations

from typing import Any, Literal, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from domain.models import RequirementsContext
from routing.router import ModelRouter
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


class RoutingDecision(BaseModel):
    destino: Literal[
        "marketing", "engenharia", "vida", "formador", "generalista", "finalizar"
    ] = Field(description="Escolhe o agente adequado ou finalizar.")
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


class StructuredRoutedRunnable(Runnable[Any, Any]):
    def __init__(self, model: "RoutedChatModel", schema: type[BaseModel]):
        self.model = model
        self.schema = schema

    def invoke_with_meta(self, input: Any, config: Any = None, **kwargs: Any) -> tuple[Any, str]:
        result, model_used = self.model.router.invoke_sync(
            self.model.task, _input_messages(input), self.model.tools, self.schema
        )
        self.model.last_model = model_used
        if isinstance(result, self.schema):
            return result, model_used
        return _structured_fallback(self.schema, result), model_used

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return self.invoke_with_meta(input, config, **kwargs)[0]

    async def ainvoke_with_meta(self, input: Any, config: Any = None, **kwargs: Any) -> tuple[Any, str]:
        result, model_used = await self.model.router.invoke(
            self.model.task, _input_messages(input), self.model.tools, self.schema
        )
        self.model.last_model = model_used
        if isinstance(result, self.schema):
            return result, model_used
        return _structured_fallback(self.schema, result), model_used

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        result, _ = await self.ainvoke_with_meta(input, config, **kwargs)
        return result


class RoutedChatModel(BaseChatModel):
    task: str
    router: ModelRouter
    tools: list[Any] = Field(default_factory=list)
    temperature: float = 0.0
    last_model: str = "router"

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return "workspace-model-router"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "RoutedChatModel":
        return self.model_copy(update={"tools": list(tools)})

    def with_structured_output(
        self, schema: type[BaseModel] | dict[str, Any], **kwargs: Any
    ) -> Runnable[Any, Any]:
        if not isinstance(schema, type) or not issubclass(schema, BaseModel):
            raise TypeError("O router requer um schema Pydantic para structured output.")
        return StructuredRoutedRunnable(self, schema)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        response, model_used = self.router.invoke_sync(
            self.task, messages, self.tools or None
        )
        self.last_model = model_used
        if not isinstance(response, AIMessage):
            response = AIMessage(content=str(response))
        response.additional_kwargs["model_used"] = model_used
        return ChatResult(generations=[ChatGeneration(message=response)])

    def invoke_with_meta(self, messages: list[BaseMessage], **kwargs: Any) -> tuple[AIMessage, str]:
        response, model_used = self.router.invoke_sync(
            self.task, messages, self.tools or None
        )
        self.last_model = model_used
        if not isinstance(response, AIMessage):
            response = AIMessage(content=str(response))
        response.additional_kwargs["model_used"] = model_used
        return response, model_used

    async def ainvoke_with_meta(
        self, messages: list[BaseMessage], **kwargs: Any
    ) -> tuple[AIMessage, str]:
        response, model_used = await self.router.invoke(
            self.task, messages, self.tools or None
        )
        self.last_model = model_used
        if not isinstance(response, AIMessage):
            response = AIMessage(content=str(response))
        response.additional_kwargs["model_used"] = model_used
        return response, model_used


def _input_messages(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [value]


def _structured_fallback(schema: type[BaseModel], value: Any) -> BaseModel:
    try:
        return schema.model_validate(value)
    except Exception:
        text = str(value)
        defaults: dict[str, Any] = {}
        for name, field in schema.model_fields.items():
            if not field.is_required():
                defaults[name] = field.default
            elif field.annotation is bool:
                defaults[name] = False
            elif field.annotation is int:
                defaults[name] = 0
            elif field.annotation in (list[str], list[dict]):
                defaults[name] = []
            else:
                defaults[name] = text
        if "destino" in defaults:
            defaults["destino"] = "finalizar"
        if "resposta_ou_instrucao" in defaults:
            defaults["resposta_ou_instrucao"] = text
        return schema.model_validate(defaults)


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
    if agent == "generalista":
        return common_research
    return common_research


def _model(
    task: str,
    *,
    temperature: float,
    tools: list[BaseTool] | None = None,
) -> RoutedChatModel:
    return RoutedChatModel(
        task=task,
        router=ModelRouter(),
        temperature=temperature,
        tools=tools or [],
    )


def specialist_models(agent: str) -> BaseChatModel:
    temperatures = {
        "marketing": 0.7,
        "vida": 0.4,
        "engenharia": 0.2,
        "formador": 0.3,
        "generalista": 0.4,
    }
    return _model(agent, temperature=temperatures[agent], tools=tools_for(agent))


def file_reader_model() -> BaseChatModel:
    return _model("file_reader", temperature=0.1)


def quality_models() -> tuple[Runnable[Any, Any], BaseChatModel]:
    quality_tools = [pesquisar_web, validar_artefactos]
    reviewer = _model("revisor", temperature=0.1).with_structured_output(
        QualityDecision
    )
    research = _model("revisor", temperature=0.1, tools=quality_tools)
    return reviewer, research


def quality_tools() -> list[BaseTool]:
    return [pesquisar_web, validar_artefactos]


def supervisor_model() -> Runnable[Any, Any]:
    return _model("supervisor", temperature=0.1).with_structured_output(
        RoutingDecision
    )


def requirements_model() -> Runnable[Any, Any]:
    return _model("supervisor", temperature=0.0).with_structured_output(
        RequirementsContext
    )
