"""Workflow assíncrono de compatibilidade para a primeira versão do projeto.

O workflow de produção continua exposto por :mod:`workflows.main_workflow`.
Este módulo mantém a API simples usada por integrações antigas, mas agora usa
os mesmos modelos e ferramentas reais dos agentes atuais.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

from agents.models import RoutingDecision, specialist_models, supervisor_model, tools_for
from agents.runtime import content_to_text, load_prompt, prepare_messages_for_model

AgentName = Literal["AgentePesquisador", "AgenteProgramador", "FINISH"]


class AgentState(TypedDict, total=False):
    """Estado partilhado pelo supervisor e pelos agentes especialistas."""

    messages: Annotated[list[BaseMessage], add_messages]
    next_agent: AgentName
    status: str
    errors: list[str]


def create_supervisor_model():
    """Cria o modelo estruturado usado pelo supervisor."""

    return supervisor_model()


async def supervisor_node(state: AgentState) -> dict[str, AgentName]:
    """Analisa o histórico e encaminha a tarefa para um agente ou termina."""

    model = create_supervisor_model()
    prompt = SystemMessage(
        content=(
            load_prompt("supervisor.md")
            + "\n\nEscolhe AgentePesquisador para factos e fontes, "
            "AgenteProgramador para código, ou finalizar quando houver resposta suficiente."
        )
    )
    decision: RoutingDecision = await model.ainvoke(
        [prompt, *state.get("messages", [])]
    )
    destination: AgentName = {
        "marketing": "AgentePesquisador",
        "vida": "AgentePesquisador",
        "formador": "AgentePesquisador",
        "engenharia": "AgenteProgramador",
        "finalizar": "FINISH",
    }.get(decision.destino, "FINISH")
    return {
        "next_agent": destination,
        "messages": (
            [AIMessage(content=decision.resposta_ou_instrucao)]
            if destination == "FINISH" and decision.resposta_ou_instrucao
            else []
        ),
    }


async def _run_agent(
    state: AgentState,
    *,
    agent: str,
    prompt_file: str,
) -> dict[str, list[BaseMessage] | str]:
    """Invoca um especialista e executa as ferramentas que ele solicitar."""

    primary, fallback = specialist_models(agent)
    tools = {tool.name: tool for tool in tools_for(agent)}
    messages = [
        SystemMessage(content=load_prompt(prompt_file)),
        *prepare_messages_for_model(list(state.get("messages", []))),
    ]
    try:
        response = await primary.ainvoke(messages)
        if not response.tool_calls:
            return {
                "messages": [AIMessage(content=content_to_text(response.content))],
                "status": "producing",
            }

        tool_messages: list[ToolMessage] = []
        for call in response.tool_calls:
            tool = tools.get(call["name"])
            if tool is None:
                raise ValueError(f"Ferramenta não permitida: {call['name']}")
            result = await tool.ainvoke(call["args"])
            tool_messages.append(
                ToolMessage(content=content_to_text(result), tool_call_id=call["id"])
            )

        final_response = await primary.ainvoke([*messages, response, *tool_messages])
        return {
            "messages": [
                *tool_messages,
                AIMessage(content=content_to_text(final_response.content)),
            ],
            "status": "producing",
        }
    except Exception as error:
        try:
            response = await fallback.ainvoke(messages)
        except Exception as fallback_error:
            detail = f"{type(error).__name__}: {error}; fallback: {type(fallback_error).__name__}: {fallback_error}"
            return {
                "messages": [AIMessage(content=f"❌ O agente {agent} falhou: {detail}")],
                "errors": [detail],
                "status": "failed",
            }
        return {
            "messages": [AIMessage(content=content_to_text(response.content))],
            "status": "producing",
        }


async def pesquisador_node(state: AgentState) -> dict[str, list[BaseMessage] | str]:
    """Pesquisa informação atual com Tavily quando o modelo a solicitar."""

    return await _run_agent(
        state,
        agent="marketing",
        prompt_file="marketing.md",
    )


async def programador_node(state: AgentState) -> dict[str, list[BaseMessage] | str]:
    """Analisa código com as ferramentas permitidas ao agente de engenharia."""

    return await _run_agent(
        state,
        agent="engenharia",
        prompt_file="engenharia.md",
    )


def route_from_supervisor(state: AgentState) -> str:
    """Converte a decisão estruturada no nome da aresta condicional."""

    return state.get("next_agent", "FINISH")


def build_graph() -> CompiledStateGraph:
    """Constrói o ciclo assíncrono Supervisor -> agente -> Supervisor."""

    builder = StateGraph(AgentState)
    builder.add_node("Supervisor", supervisor_node)
    builder.add_node("AgentePesquisador", pesquisador_node)
    builder.add_node("AgenteProgramador", programador_node)
    builder.add_edge(START, "Supervisor")
    builder.add_conditional_edges(
        "Supervisor",
        route_from_supervisor,
        {
            "AgentePesquisador": "AgentePesquisador",
            "AgenteProgramador": "AgenteProgramador",
            "FINISH": END,
        },
    )
    builder.add_edge("AgentePesquisador", "Supervisor")
    builder.add_edge("AgenteProgramador", "Supervisor")
    return builder.compile()


async def main() -> None:
    """Executa uma interface de terminal assíncrona para testar o grafo."""

    graph = build_graph()
    print("Multi-agente pronto. Escreve 'sair' para terminar.")
    while True:
        user_input = (await asyncio.to_thread(input, "\nTu> ")).strip()
        if user_input.lower() in {"sair", "exit", "quit"}:
            break
        if not user_input:
            continue
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=user_input)]},
            config={"recursion_limit": 20},
        )
        messages = result.get("messages", [])
        if messages:
            print(f"\nSistema> {content_to_text(messages[-1].content)}")


if __name__ == "__main__":
    asyncio.run(main())
