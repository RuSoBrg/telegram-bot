import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from typing import Annotated, Literal
from typing_extensions import TypedDict
from pydantic import BaseModel, Field

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_openai import AzureChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode, tools_condition

from tools.code_tools import ler_ficheiro_codigo, listar_ficheiros_projeto
from tools.file_tools import (
    gerar_documento_pdf,
    gerar_documento_word,
    gerar_apresentacao_powerpoint,
    gerar_audio,
    gerar_fotografia,
    gerar_musica,
    gerar_video,
)

load_dotenv()
logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent

class State(TypedDict):
    messages: Annotated[list, add_messages]
    next_agent: str
    instruction: str
    quality_feedback: str
    quality_iterations: int
    quality_target: str

class RoutingDecision(BaseModel):
    destino: Literal["marketing", "engenharia", "vida", "formador", "finalizar"] = Field(
        description="Escolhe 'marketing' para TikTok/Growth, 'engenharia' para código/Flutter/C#, 'vida' para rotinas pessoais, 'formador' para formação e 'finalizar' para responder diretamente."
    )
    resposta_ou_instrucao: str = Field(
        description="Se 'finalizar', escreve a resposta ao utilizador. Caso contrário, escreve a instrução para o agente especializado."
    )

class QualityDecision(BaseModel):
    aprovado: bool = Field(
        description="True apenas quando o resultado estiver profissional, completo, correto e pronto a entregar."
    )
    feedback: str = Field(
        description="Feedback acionável para o implementador corrigir o resultado. Vazio se aprovado."
    )

# Modelos Híbridos (com max_retries=3 para tolerância a falhas na Google)
llm_supervisor = AzureChatOpenAI(
    azure_deployment=os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME"),
    api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
    temperature=0.1,
)
supervisor_router = llm_supervisor.with_structured_output(RoutingDecision)

gemini_model_name = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
gemini_fallback_model_name = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")

ferramentas_ficheiros = [
    gerar_documento_word,
    gerar_apresentacao_powerpoint,
    gerar_documento_pdf,
    gerar_fotografia,
    gerar_video,
    gerar_audio,
    gerar_musica,
]
ferramentas_formador = ferramentas_ficheiros
ferramentas_engenharia = [
    ler_ficheiro_codigo,
    listar_ficheiros_projeto,
    *ferramentas_ficheiros,
]

llm_marketing = ChatGoogleGenerativeAI(model=gemini_model_name, temperature=0.7, max_retries=1).bind_tools(
    ferramentas_ficheiros
)
llm_marketing_fallback = ChatGoogleGenerativeAI(
    model=gemini_fallback_model_name, temperature=0.7, max_retries=2
).bind_tools(ferramentas_ficheiros)
llm_vida = ChatGoogleGenerativeAI(model=gemini_model_name, temperature=0.4, max_retries=1).bind_tools(
    ferramentas_ficheiros
)
llm_vida_fallback = ChatGoogleGenerativeAI(
    model=gemini_fallback_model_name, temperature=0.4, max_retries=2
).bind_tools(ferramentas_ficheiros)
llm_engenharia = ChatGoogleGenerativeAI(
    model=gemini_model_name, temperature=0.2, max_retries=1
).bind_tools(ferramentas_engenharia)
llm_engenharia_fallback = ChatGoogleGenerativeAI(
    model=gemini_fallback_model_name, temperature=0.2, max_retries=2
).bind_tools(ferramentas_engenharia)
llm_formador = ChatGoogleGenerativeAI(
    model=gemini_model_name, temperature=0.3, max_retries=1
).bind_tools(ferramentas_formador)
llm_formador_fallback = ChatGoogleGenerativeAI(
    model=gemini_fallback_model_name, temperature=0.3, max_retries=2
).bind_tools(ferramentas_formador)
llm_quality = ChatGoogleGenerativeAI(
    model=gemini_model_name, temperature=0.1, max_retries=1
).with_structured_output(QualityDecision)
llm_quality_fallback = ChatGoogleGenerativeAI(
    model=gemini_fallback_model_name, temperature=0.1, max_retries=2
).with_structured_output(QualityDecision)
MAX_QUALITY_ITERATIONS = int(os.environ.get("MAX_QUALITY_ITERATIONS", "3"))


def _is_transient_google_error(error: Exception) -> bool:
    """Identifica erros temporários que justificam trocar de modelo."""
    status_code = getattr(error, "status_code", None)
    if status_code in {429, 500, 502, 503, 504}:
        return True
    error_text = str(error).upper()
    return any(
        marker in error_text
        for marker in ("429", "500", "502", "503", "504", "UNAVAILABLE", "RESOURCE_EXHAUSTED")
    )


def _invoke_google(primary, fallback, messages):
    try:
        return primary.invoke(messages)
    except Exception as error:
        if not _is_transient_google_error(error):
            raise
        logger.warning(
            "Modelo Gemini primário indisponível; a tentar fallback %s",
            gemini_fallback_model_name,
        )
        return fallback.invoke(messages)


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)

def ler_prompt(nome_ficheiro: str) -> str:
    caminho = BASE_DIR / "prompts" / nome_ficheiro
    try:
        return caminho.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("Prompt não encontrado: %s", caminho)
        raise RuntimeError(f"Prompt obrigatório não encontrado: {nome_ficheiro}") from None
    except OSError:
        logger.exception("Falha ao ler prompt: %s", caminho)
        raise RuntimeError(f"Não foi possível carregar o prompt: {nome_ficheiro}") from None

def supervisor_node(state: State):
    sys_prompt = ler_prompt("supervisor.md")
    instrucao_extra = (
        "\n\nIMPORTANTE: És o CEO/Router. Decide entre 'marketing', 'engenharia', "
        "'vida', 'formador' ou 'finalizar'."
    )
    
    mensagens = [SystemMessage(content=sys_prompt + instrucao_extra)] + state["messages"]
    try:
        decisao = supervisor_router.invoke(mensagens)
    except Exception:
        logger.exception("Falha no routing do supervisor")
        return {
            "messages": [AIMessage(content="⚠️ O supervisor não está disponível neste momento.")],
            "next_agent": "finalizar",
            "instruction": "",
            "quality_feedback": "",
            "quality_iterations": 0,
            "quality_target": "",
        }
    
    if decisao.destino == "finalizar":
        return {
            "messages": [AIMessage(content=f"🧠 [CEO / GPT-4o]:\n{decisao.resposta_ou_instrucao}")], 
            "next_agent": "finalizar",
            "instruction": "",
            "quality_feedback": "",
            "quality_iterations": 0,
            "quality_target": "",
        }
    else:
        return {
            "next_agent": decisao.destino,
            "instruction": decisao.resposta_ou_instrucao,
            "quality_feedback": "",
            "quality_iterations": 0,
            "quality_target": decisao.destino,
        }

def marketing_node(state: State):
    sys_prompt = ler_prompt("marketing.md")
    instruction = state.get("instruction", "")
    if instruction:
        sys_prompt += f"\n\nInstrução do CEO: {instruction}"
        
    mensagens = [SystemMessage(content=sys_prompt)] + state["messages"]
    try:
        resposta = _invoke_google(llm_marketing, llm_marketing_fallback, mensagens)
        if resposta.tool_calls:
            return {"messages": [resposta]}
        conteudo = _content_to_text(resposta.content)
    except Exception:
        logger.exception("Falha no agente de marketing")
        conteudo = "⚠️ O agente de marketing não está disponível neste momento. Tenta novamente."
        
    return {"messages": [AIMessage(content=f"🎬 [CMO / Gemini]:\n{conteudo}")]}

def vida_node(state: State):
    sys_prompt = ler_prompt("vida.md")
    instruction = state.get("instruction", "")
    if instruction:
        sys_prompt += f"\n\nInstrução do CEO: {instruction}"
        
    mensagens = [SystemMessage(content=sys_prompt)] + state["messages"]
    try:
        resposta = _invoke_google(llm_vida, llm_vida_fallback, mensagens)
        if resposta.tool_calls:
            return {"messages": [resposta]}
        conteudo = _content_to_text(resposta.content)
    except Exception:
        logger.exception("Falha no agente de vida")
        conteudo = "⚠️ O agente de vida não está disponível neste momento. Tenta novamente."
        
    return {"messages": [AIMessage(content=f"🏠 [COO Vida / Gemini]:\n{conteudo}")]}

def engenharia_node(state: State):
    sys_prompt = ler_prompt("engenharia.md")
    instruction = state.get("instruction", "")
    if instruction:
        sys_prompt += f"\n\nInstrução do CEO: {instruction}"
        
    mensagens = [SystemMessage(content=sys_prompt)] + state["messages"]
    try:
        resposta = _invoke_google(llm_engenharia, llm_engenharia_fallback, mensagens)
    except Exception:
        logger.exception("Falha no agente de engenharia")
        return {"messages": [AIMessage(content="⚠️ O agente de engenharia não está disponível neste momento.")]}
    
    if not resposta.tool_calls:
        resposta = AIMessage(
            content=f"💻 [CTO / Gemini]:\n{_content_to_text(resposta.content)}"
        )
        
    return {"messages": [resposta]}

def formador_node(state: State):
    sys_prompt = ler_prompt("formador.md")
    instruction = state.get("instruction", "")
    feedback = state.get("quality_feedback", "")
    if instruction:
        sys_prompt += f"\n\nInstrução do CEO: {instruction}"
    if feedback:
        sys_prompt += f"\n\nFeedback da supervisão de qualidade a corrigir:\n{feedback}"

    mensagens = [SystemMessage(content=sys_prompt)] + state["messages"]
    try:
        resposta = _invoke_google(llm_formador, llm_formador_fallback, mensagens)
    except Exception:
        logger.exception("Falha no agente formador")
        return {"messages": [AIMessage(content="⚠️ O agente formador não está disponível neste momento.")]}

    if not resposta.tool_calls:
        resposta = AIMessage(
            content=f"🎓 [FORMADOR / Gemini]:\n{_content_to_text(resposta.content)}"
        )
    return {"messages": [resposta]}


def quality_node(state: State):
    target = state.get("quality_target", "agente")
    iteration = state.get("quality_iterations", 0)
    if iteration >= MAX_QUALITY_ITERATIONS:
        logger.warning("Limite de iterações de qualidade atingido para %s", target)
        return {"quality_feedback": "", "quality_iterations": iteration}

    review_prompt = ler_prompt("qualidade.md")
    review_prompt += (
        f"\n\nAgente a rever: {target}. Iteração atual: {iteration + 1}."
        "\nAvalia apenas o último output produzido pelo agente e os artefactos associados."
    )
    try:
        decisao = _invoke_google(
            llm_quality,
            llm_quality_fallback,
            [SystemMessage(content=review_prompt)] + state["messages"],
        )
    except Exception:
        logger.exception("Falha na supervisão de qualidade")
        return {
            "quality_feedback": "A revisão automática falhou. Faz uma revisão final completa e entrega apenas material verificável.",
            "quality_iterations": iteration + 1,
        }

    if decisao.aprovado:
        return {"quality_feedback": "", "quality_iterations": iteration + 1}
    return {
        "quality_feedback": decisao.feedback or "Corrige completude, rigor e apresentação profissional.",
        "quality_iterations": iteration + 1,
    }


def route_after_supervisor(
    state: State,
) -> Literal["marketing", "engenharia", "vida", "formador", "__end__"]:
    destino = state.get("next_agent")
    if destino in ["marketing", "engenharia", "vida", "formador"]:
        return destino
    return "__end__"


def route_after_quality(
    state: State,
) -> Literal["marketing", "engenharia", "vida", "formador", "__end__"]:
    target = state.get("quality_target", "")
    if (
        state.get("quality_feedback")
        and state.get("quality_iterations", 0) < MAX_QUALITY_ITERATIONS
        and target in {"marketing", "engenharia", "vida", "formador"}
    ):
        return target
    return "__end__"

builder = StateGraph(State)
builder.add_node("supervisor", supervisor_node)
builder.add_node("marketing", marketing_node)
builder.add_node("engenharia", engenharia_node)
builder.add_node("vida", vida_node)
builder.add_node("formador", formador_node)
builder.add_node("qualidade", quality_node)

tool_node = ToolNode(ferramentas_engenharia)
builder.add_node("tools_engenharia", tool_node)
builder.add_node("tools_marketing", ToolNode(ferramentas_ficheiros))
builder.add_node("tools_vida", ToolNode(ferramentas_ficheiros))

builder.add_edge(START, "supervisor")
builder.add_conditional_edges(
    "supervisor",
    route_after_supervisor,
    {
        "marketing": "marketing",
        "engenharia": "engenharia",
        "vida": "vida",
        "formador": "formador",
        "__end__": END
    }
)

builder.add_conditional_edges(
    "marketing",
    tools_condition,
    {"tools": "tools_marketing", "__end__": "qualidade"},
)
builder.add_edge("tools_marketing", "marketing")
builder.add_conditional_edges(
    "vida",
    tools_condition,
    {"tools": "tools_vida", "__end__": "qualidade"},
)
builder.add_edge("tools_vida", "vida")

builder.add_conditional_edges(
    "engenharia",
    tools_condition,
    {
        "tools": "tools_engenharia",
        "__end__": "qualidade"
    }
)
builder.add_edge("tools_engenharia", "engenharia")
builder.add_node("tools_formador", ToolNode(ferramentas_formador))
builder.add_conditional_edges(
    "formador",
    tools_condition,
    {"tools": "tools_formador", "__end__": "qualidade"},
)
builder.add_edge("tools_formador", "formador")
builder.add_conditional_edges(
    "qualidade",
    route_after_quality,
    {
        "marketing": "marketing",
        "engenharia": "engenharia",
        "vida": "vida",
        "formador": "formador",
        "__end__": END,
    },
)

memoria = MemorySaver()
grafo = builder.compile(checkpointer=memoria)
