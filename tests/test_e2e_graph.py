from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from routing.catalog import ModelEntry
from routing.router import ModelRouter


def _response(text, tool_calls=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=tool_calls or [])
            )
        ]
    )


def _graph(router):
    builder = StateGraph(dict)

    def node(state):
        response, used = router.invoke_sync(
            "test", state["messages"], tools=state.get("tools")
        )
        return {"messages": [response], "model_used": used}

    builder.add_node("router", node)
    builder.add_edge(START, "router")
    builder.add_edge("router", END)
    return builder.compile(checkpointer=MemorySaver())


def test_e2e_graph_always_returns_text_and_quotes_original_request(tmp_path):
    catalog = {"test": [ModelEntry("fake", "one", "", (), 1, 100)]}
    router = ModelRouter(catalog=catalog, budget_seconds=2)
    graph = _graph(router)
    request = "pergunta original"
    with patch(
        "routing.router.litellm.acompletion",
        new=AsyncMock(return_value=_response("resposta")),
    ):
        state = graph.invoke(
            {"messages": [HumanMessage(content=request)]},
            {"configurable": {"thread_id": "e2e-ok"}},
        )
    assert state["messages"][0].content


def test_e2e_graph_all_429_uses_local_fallback_and_blocks_next_call(tmp_path):
    catalog = {
        "test": [
            ModelEntry("fake", "one", "", (), 1, 100),
            ModelEntry("fake", "two", "", (), 1, 90),
        ]
    }
    router = ModelRouter(catalog=catalog, budget_seconds=2)
    graph = _graph(router)
    quota = __import__("litellm").RateLimitError("daily quota", "fake", "fake")
    with patch("routing.router.litellm.acompletion", new=AsyncMock(side_effect=quota)) as completion:
        first = graph.invoke(
            {"messages": [HumanMessage(content="pergunta original")]},
            {"configurable": {"thread_id": "e2e-429"}},
        )
        second = graph.invoke(
            {"messages": [HumanMessage(content="pergunta original")]},
            {"configurable": {"thread_id": "e2e-429-2"}},
        )
    assert first["messages"][0]
    assert "pergunta original" in first["messages"][0]
    assert completion.await_count >= 3
    assert second["messages"][0]


def test_e2e_graph_executes_requested_tool_and_returns_final_text(tmp_path):
    @tool
    def add_one(value: int) -> str:
        """Adiciona um ao valor."""
        return str(value + 1)

    catalog = {"test": [ModelEntry("fake", "one", "", ("tools",), 1, 100)]}
    router = ModelRouter(catalog=catalog, budget_seconds=2)

    def node(state):
        messages = state["messages"]
        response, _ = router.invoke_sync("test", messages, tools=[add_one])
        if isinstance(response, AIMessage) and response.tool_calls:
            call = response.tool_calls[0]
            result = add_one.invoke(call["args"])
            messages = [
                *messages,
                response,
                ToolMessage(content=result, tool_call_id=call["id"]),
            ]
            response, _ = router.invoke_sync("test", messages, tools=[add_one])
        return {"messages": [response]}

    builder = StateGraph(dict)
    builder.add_node("router", node)
    builder.add_edge(START, "router")
    builder.add_edge("router", END)
    graph = builder.compile(checkpointer=MemorySaver())
    first = _response(
        "",
        [{"id": "call-1", "type": "function", "function": {
            "name": "add_one", "arguments": '{"value": 2}'
        }}],
    )
    with patch(
        "routing.router.litellm.acompletion",
        new=AsyncMock(side_effect=[first, _response("resultado: 3")]),
    ) as completion:
        state = graph.invoke(
            {"messages": [HumanMessage(content="soma um")]},
            {"configurable": {"thread_id": "e2e-tool"}},
        )
    assert state["messages"][0].content == "resultado: 3"
    assert completion.await_count == 2
