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

load_dotenv()
logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent

class State(TypedDict):
    messages: Annotated[list, add_messages]
    next_agent: str
    instruction: str

class RoutingDecision(BaseModel):
    destino: Literal["marketing", "engenharia", "vida", "finalizar"] = Field(
        description="Escolhe 'marketing' para TikTok/Growth. Escolhe 'engenharia' para código/Flutter/C#. Escolhe 'vida' para rotinas pessoais, veículos, CCP ou carreira. Escolhe 'finalizar' para responder diretamente."
    )
    resposta_ou_instrucao: str = Field(
        description="Se 'finalizar', escreve a resposta ao utilizador. Caso contrário, escreve a instrução para o agente especializado."
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

llm_marketing = ChatGoogleGenerativeAI(model=gemini_model_name, temperature=0.7, max_retries=1)
llm_marketing_fallback = ChatGoogleGenerativeAI(
    model=gemini_fallback_model_name, temperature=0.7, max_retries=2
)
llm_vida = ChatGoogleGenerativeAI(model=gemini_model_name, temperature=0.4, max_retries=1)
llm_vida_fallback = ChatGoogleGenerativeAI(
    model=gemini_fallback_model_name, temperature=0.4, max_retries=2
)

ferramentas_engenharia = [ler_ficheiro_codigo, listar_ficheiros_projeto]
llm_engenharia = ChatGoogleGenerativeAI(
    model=gemini_model_name, temperature=0.2, max_retries=1
).bind_tools(ferramentas_engenharia)
llm_engenharia_fallback = ChatGoogleGenerativeAI(
    model=gemini_fallback_model_name, temperature=0.2, max_retries=2
).bind_tools(ferramentas_engenharia)


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
    instrucao_extra = "\n\nIMPORTANTE: És o CEO/Router. Decide entre 'marketing', 'engenharia', 'vida' ou 'finalizar'."
    
    mensagens = [SystemMessage(content=sys_prompt + instrucao_extra)] + state["messages"]
    try:
        decisao = supervisor_router.invoke(mensagens)
    except Exception:
        logger.exception("Falha no routing do supervisor")
        return {
            "messages": [AIMessage(content="⚠️ O supervisor não está disponível neste momento.")],
            "next_agent": "finalizar",
            "instruction": ""
        }
    
    if decisao.destino == "finalizar":
        return {
            "messages": [AIMessage(content=f"🧠 [CEO / GPT-4o]:\n{decisao.resposta_ou_instrucao}")], 
            "next_agent": "finalizar",
            "instruction": ""
        }
    else:
        return {
            "next_agent": decisao.destino,
            "instruction": decisao.resposta_ou_instrucao
        }

def marketing_node(state: State):
    sys_prompt = ler_prompt("marketing.md")
    instruction = state.get("instruction", "")
    if instruction:
        sys_prompt += f"\n\nInstrução do CEO: {instruction}"
        
    mensagens = [SystemMessage(content=sys_prompt)] + state["messages"]
    try:
        resposta = _invoke_google(llm_marketing, llm_marketing_fallback, mensagens)
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

def route_after_supervisor(state: State) -> Literal["marketing", "engenharia", "vida", "__end__"]:
    destino = state.get("next_agent")
    if destino in ["marketing", "engenharia", "vida"]:
        return destino
    return "__end__"

builder = StateGraph(State)
builder.add_node("supervisor", supervisor_node)
builder.add_node("marketing", marketing_node)
builder.add_node("engenharia", engenharia_node)
builder.add_node("vida", vida_node)

tool_node = ToolNode(ferramentas_engenharia)
builder.add_node("tools_engenharia", tool_node)

builder.add_edge(START, "supervisor")
builder.add_conditional_edges(
    "supervisor",
    route_after_supervisor,
    {
        "marketing": "marketing",
        "engenharia": "engenharia",
        "vida": "vida",
        "__end__": END
    }
)

builder.add_edge("marketing", END)
builder.add_edge("vida", END)

builder.add_conditional_edges(
    "engenharia",
    tools_condition,
    {
        "tools": "tools_engenharia",
        "__end__": END
    }
)
builder.add_edge("tools_engenharia", "engenharia")

memoria = MemorySaver()
grafo = builder.compile(checkpointer=memoria)
