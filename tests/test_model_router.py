from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

import litellm
from routing.availability import AvailabilityStore
from routing.catalog import ModelEntry
from routing.router import ModelRouter
from routing.errors import ErrorKind, classify_error
from agents.models import RoutedChatModel


def _tool():
    return {
        "type": "function",
        "function": {"name": "test_tool", "description": "", "parameters": {}},
    }


def _response(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=[]))]
    )


def _catalog(count=3, capabilities=("tools",)):
    return {
        "test": [
            ModelEntry("fake", f"model-{index}", "", capabilities, 1, 100 - index)
            for index in range(count)
        ]
    }


def _router(tmp_path, count=3, capabilities=("tools",)):
    return ModelRouter(
        catalog=_catalog(count, capabilities),
        availability=AvailabilityStore(str(tmp_path / "availability.db")),
        budget_seconds=90,
    )


def test_fallbacks_and_persists_failures(tmp_path):
    router = _router(tmp_path)
    rate = litellm.RateLimitError("daily quota", "fake", "fake", headers={"Retry-After": "60"})
    timeout = litellm.Timeout("timeout", "fake/model-1", "fake")
    with patch(
        "routing.router.litellm.acompletion",
        new=AsyncMock(side_effect=[rate, timeout, _response("terceiro")]),
    ) as completion:
        result, used = __import__("asyncio").run(
            router.invoke("test", [HumanMessage(content="pedido")], tools=[_tool()])
        )

    assert result.content == "terceiro"
    assert used == "fake/model-2"
    assert completion.await_count == 3
    snapshot = router.availability.snapshot()
    assert {item["model"] for item in snapshot} == {"model-0", "model-1"}

    with patch("routing.router.litellm.acompletion", new=AsyncMock(return_value=_response("novo"))) as second:
        __import__("asyncio").run(
            router.invoke("test", [HumanMessage(content="pedido")], tools=[_tool()])
        )
    assert second.await_count == 1
    assert second.await_args.kwargs["model"].endswith("model-2")


def test_model_is_retried_after_unavailable_until(tmp_path):
    router = _router(tmp_path, 1)
    until = datetime.now(timezone.utc) - timedelta(seconds=1)
    router.availability.mark_unavailable("fake", "model-0", until, "timeout", "old")
    with patch("routing.router.litellm.acompletion", new=AsyncMock(return_value=_response("ok"))) as completion:
        __import__("asyncio").run(router.invoke("test", [HumanMessage(content="pedido")], tools=[_tool()]))
    assert completion.await_count == 1


def test_eighth_model_and_local_fallback(tmp_path):
    router = _router(tmp_path, 8)
    with patch(
        "routing.router.litellm.acompletion",
        new=AsyncMock(side_effect=[RuntimeError("falha")] * 7 + [_response("oitavo")]),
    ):
        result, used = __import__("asyncio").run(router.invoke("test", [HumanMessage(content="pedido")], tools=[_tool()]))
    assert result.content == "oitavo"
    assert used == "fake/model-7"

    all_failed = _router(tmp_path / "all", 2)
    with patch("routing.router.litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("falha"))):
        result, used =         __import__("asyncio").run(all_failed.invoke("test", [HumanMessage(content="pedido")], tools=[_tool()]))
    assert result
    assert used == "local-fallback"


def test_auth_disables_provider_and_not_found_only_model(tmp_path):
    router = _router(tmp_path, 2)
    with patch(
        "routing.router.litellm.acompletion",
        new=AsyncMock(side_effect=[
            litellm.AuthenticationError("bad key", "fake", "fake"),
            _response("ok"),
        ]),
    ):
        __import__("asyncio").run(router.invoke("test", [HumanMessage(content="pedido")], tools=[_tool()]))
    assert router.availability.snapshot()[0]["reason"] == "auth"

    not_found = _router(tmp_path / "not-found", 2)
    with patch(
        "routing.router.litellm.acompletion",
        new=AsyncMock(side_effect=[
            litellm.NotFoundError("model not found", "fake", "fake"),
            _response("ok"),
        ]),
    ):
        __import__("asyncio").run(
            not_found.invoke("test", [HumanMessage(content="pedido")], tools=[_tool()])
        )
    snapshot = not_found.availability.snapshot()
    assert snapshot[0]["model"] == "model-0"
    assert snapshot[0]["reason"] == "model_not_found"


class Output(BaseModel):
    answer: str


def test_structured_native_failure_uses_json_manual(tmp_path):
    router = _router(tmp_path, 1, ("structured_output",))
    with patch(
        "routing.router.litellm.acompletion",
        new=AsyncMock(side_effect=[
            ValueError("structured output parse failed"),
            _response('{"answer":"manual"}'),
        ]),
    ) as completion:
        result, _ = __import__("asyncio").run(
            router.invoke("test", [HumanMessage(content="pedido")], schema=Output)
        )
    assert result.answer == "manual"
    assert completion.await_count == 2


def test_tools_only_considers_capable_models(tmp_path):
    router = ModelRouter(
        catalog={
            "test": [
                ModelEntry("fake", "no-tools", "", ("structured_output",), 1, 100),
                ModelEntry("fake", "tools", "", ("tools",), 1, 50),
            ]
        },
        availability=AvailabilityStore(str(tmp_path / "availability.db")),
    )
    with patch("routing.router.litellm.acompletion", new=AsyncMock(return_value=_response("ok"))) as completion:
        __import__("asyncio").run(router.invoke("test", [HumanMessage(content="pedido")], tools=[_tool()]))
    assert completion.await_args.kwargs["model"].endswith("/tools")


def test_tool_calls_are_converted_from_litellm_objects():
    function = SimpleNamespace(name="test_tool", arguments='{"value": 2}')
    message = SimpleNamespace(
        content="",
        tool_calls=[SimpleNamespace(id="call-1", function=function)],
    )
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])

    converted = ModelRouter._message(response)

    assert converted.tool_calls == [
        {"name": "test_tool", "args": {"value": 2}, "id": "call-1", "type": "tool_call"}
    ]


def test_messages_preserve_openai_tool_protocol():
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "test_tool", "args": {"value": 2}, "id": "call-1", "type": "tool_call"}
            ],
        ),
        ToolMessage(content=[{"text": "feito"}], tool_call_id="call-1"),
    ]

    payload = __import__("routing.router", fromlist=["_messages"])._messages(messages)

    assert payload[0]["tool_calls"][0]["function"]["arguments"] == '{"value": 2}'
    assert payload[1]["role"] == "tool"
    assert payload[1]["tool_call_id"] == "call-1"
    assert isinstance(payload[1]["content"], list)


def test_structured_provider_errors_are_not_retried(tmp_path):
    router = _router(tmp_path, 1, ("structured_output",))
    with patch(
        "routing.router.litellm.acompletion",
        new=AsyncMock(side_effect=litellm.RateLimitError("daily quota", "fake", "fake")),
    ) as completion:
        __import__("asyncio").run(
            router.invoke("test", [HumanMessage(content="pedido")], schema=Output)
        )
    assert completion.await_count == 1


def test_unknown_only_gets_cooldown_after_three_failures():
    first = classify_error(RuntimeError("unexpected"), fail_count=0)
    third = classify_error(RuntimeError("unexpected"), fail_count=3)
    assert first.kind == ErrorKind.UNKNOWN
    assert first.retry_at is None
    assert third.retry_at is not None


def test_router_sorts_by_score_and_retries_two_earliest_blocked(tmp_path):
    availability = AvailabilityStore(str(tmp_path / "availability.db"))
    catalog = {
        "test": [
            ModelEntry("fake", "low", "", ("tools",), 1, 10),
            ModelEntry("fake", "high", "", ("tools",), 1, 100),
        ]
    }
    now = datetime.now(timezone.utc)
    availability.mark_unavailable("fake", "high", now + timedelta(seconds=30), "timeout", "x")
    availability.mark_unavailable("fake", "low", now + timedelta(seconds=10), "timeout", "x")
    router = ModelRouter(catalog=catalog, availability=availability)
    with patch("routing.router.litellm.acompletion", new=AsyncMock(return_value=_response("ok"))) as completion:
        __import__("asyncio").run(
            router.invoke("test", [HumanMessage(content="pedido")], tools=[_tool()])
        )
    assert completion.await_count == 1
    assert completion.await_args.kwargs["model"].endswith("/low")


def test_fallback_does_not_quote_synthetic_prompt():
    synthetic = HumanMessage(
        content="Prossegue com o próximo passo da análise.",
        additional_kwargs={"synthetic": True},
    )
    fallback = ModelRouter._fallback([synthetic], "test")
    assert "Prossegue com o próximo passo" not in fallback
    assert "o teu pedido" in fallback


def test_concurrent_requests_return_their_own_model_metadata():
    class FakeRouter(ModelRouter):
        async def invoke(self, task, messages, tools=None, schema=None):
            model = messages[0].content
            return AIMessage(content=model), model

    model = RoutedChatModel(task="test", router=FakeRouter.__new__(FakeRouter))

    async def run():
        return await __import__("asyncio").gather(
            model.ainvoke_with_meta([HumanMessage(content="model-a")]),
            model.ainvoke_with_meta([HumanMessage(content="model-b")]),
        )

    results = __import__("asyncio").run(run())
    assert [item[1] for item in results] == ["model-a", "model-b"]
