from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import HumanMessage
from pydantic import BaseModel

import litellm
from routing.availability import AvailabilityStore
from routing.catalog import ModelEntry
from routing.router import ModelRouter


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
            RuntimeError("structured unsupported"),
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
