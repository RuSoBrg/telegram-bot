import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from domain.db import ArtifactStore as ArtifactVersionStore, WorkflowStore

with (
    patch.object(WorkflowStore, "__init__", lambda self, _database_url=None: None),
    patch.object(ArtifactVersionStore, "__init__", lambda self, _database_url=None: None),
):
    import bot
from agents.quality_reviewer import _failure_command, _limit_command
from workflows.main_workflow import GRAPH_RECURSION_LIMIT, _interrupt_before_nodes


class _FakeContext:
    def __init__(self):
        self.bot = SimpleNamespace(send_message=AsyncMock())


def _update():
    return SimpleNamespace(effective_chat=SimpleNamespace(id=42))


def test_send_workflow_message_never_raises_name_error():
    context = _FakeContext()

    asyncio.run(bot._send_workflow_message(context, 42, "Resposta", False, [], {}))
    asyncio.run(
        bot._send_workflow_message(context, 42, None, True, [], {"status": "approved"})
    )

    assert context.bot.send_message.await_count == 2


def test_workflow_exception_always_sends_user_message(monkeypatch):
    context = _FakeContext()
    monkeypatch.setattr(
        bot, "_stream_graph", AsyncMock(side_effect=RuntimeError("falha"))
    )

    asyncio.run(
        bot._run_graph_events(
            _update(),
            context,
            {"messages": [HumanMessage(content="pedido simples")]},
            "thread",
            "request",
        )
    )

    text = context.bot.send_message.await_args.kwargs["text"]
    assert "pedido simples" in text
    assert "RuntimeError" not in text


def test_quality_failure_keeps_last_specialist_response():
    result = _failure_command(
        "timeout",
        0,
        {"messages": [AIMessage(content="Resposta útil do especialista.")]},
    )

    assert result.update["messages"][0].content.startswith("Resposta útil")
    assert "Revisão automática não concluída" in result.update["messages"][0].content
    assert result.update["errors"]


def test_quality_limit_keeps_last_specialist_response():
    result = _limit_command(
        2,
        2,
        {"messages": [AIMessage(content="Resposta antes do limite.")]},
    )

    assert "Resposta antes do limite." in result.update["messages"][0].content
    assert "Revisão automática não concluída" in result.update["messages"][0].content


def test_simple_requests_do_not_pause_for_approval(monkeypatch):
    monkeypatch.setenv("REQUIRE_HUMAN_APPROVAL", "false")
    assert _interrupt_before_nodes() == []
    assert GRAPH_RECURSION_LIMIT == 30

    monkeypatch.setenv("REQUIRE_HUMAN_APPROVAL", "true")
    assert _interrupt_before_nodes() == ["tools_engenharia"]


def test_model_footer_is_configurable():
    assert bot._with_model_footer(
        "Resposta", {"agent_used": "engenharia", "model_used": "openai/gpt-4o"}
    ).endswith("— engenharia · openai/gpt-4o")

    import os

    previous = os.environ.get("SHOW_MODEL_FOOTER")
    os.environ["SHOW_MODEL_FOOTER"] = "false"
    try:
        assert bot._with_model_footer(
            "Resposta", {"agent_used": "engenharia", "model_used": "openai/gpt-4o"}
        ) == "Resposta"
    finally:
        if previous is None:
            os.environ.pop("SHOW_MODEL_FOOTER", None)
        else:
            os.environ["SHOW_MODEL_FOOTER"] = previous
