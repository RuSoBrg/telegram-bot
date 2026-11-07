import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from telegram import Update

import bot
from app.service import WorkflowService, extract_artifacts_from_state, run_job
from app.settings import Settings
from domain.models import WorkflowRun, WorkflowStatus
from domain.db import WorkflowStore


def test_allowed_users_required_on_startup(monkeypatch):
    monkeypatch.setenv("ALLOWED_USER_IDS", "")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "")
    settings = Settings()
    with pytest.raises(RuntimeError, match="ALLOWED_USER_IDS não está configurado"):
        settings.require_allowed_users()


def test_reject_unauthorized_logs_only_id(caplog):
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=999999),
        effective_chat=SimpleNamespace(id=123),
    )
    with caplog.at_level("WARNING"):
        asyncio.run(bot._reject_unauthorized(update, context))

    assert "Tentativa de acesso não autorizada: user_id=999999" in caplog.text
    context.bot.send_message.assert_awaited_once_with(
        chat_id=123,
        text="Não tens autorização para usar este bot.",
    )


def test_three_files_in_same_step_and_after_approval(tmp_path):
    # Três artefactos gerados no mesmo passo
    art1 = json.dumps({"tipo": "ficheiro_gerado", "filename": "relatorio.pdf", "size_bytes": 1024, "uri": "db://artifacts/1"})
    art2 = json.dumps({"tipo": "ficheiro_gerado", "filename": "dados.xlsx", "size_bytes": 2048, "uri": "db://artifacts/2"})
    art3 = json.dumps({"tipo": "ficheiro_gerado", "filename": "slides.pptx", "size_bytes": 4096, "uri": "db://artifacts/3"})

    state = {
        "messages": [
            ToolMessage(content=art1, tool_call_id="call_1"),
            ToolMessage(content=art2, tool_call_id="call_2"),
            ToolMessage(content=art3, tool_call_id="call_3"),
        ],
        "status": "approved",
    }
    extracted = extract_artifacts_from_state(state)
    assert len(extracted) == 3
    filenames = {item["filename"] for item in extracted}
    assert filenames == {"relatorio.pdf", "dados.xlsx", "slides.pptx"}


def test_workflow_status_completed_distinct_from_approved():
    # Sem aprovação humana explícita -> COMPLETED
    status_direct = bot._workflow_status(False, {"status": "success"})
    assert status_direct == WorkflowStatus.COMPLETED

    # Com aprovação humana -> APPROVED
    status_approved = bot._workflow_status(False, {"status": "approved"})
    assert status_approved == WorkflowStatus.APPROVED

    # Requer aprovação -> APPROVAL_REQUIRED
    status_pending = bot._workflow_status(True, {})
    assert status_pending == WorkflowStatus.APPROVAL_REQUIRED


def test_inline_approval_callback_validates_owner_and_idempotency(monkeypatch, tmp_path):
    store = WorkflowStore(str(tmp_path / "test.db"))
    monkeypatch.setattr(bot, "workflow_store", store)
    monkeypatch.setenv("ALLOWED_USER_IDS", "100,200")

    run = WorkflowRun(
        request_id="req-123",
        workflow="auto",
        user_id="100",
        status=WorkflowStatus.APPROVAL_REQUIRED,
        metadata={"thread_id": "chat1:req-123"},
    )
    store.save(run)

    # 1. Não autorizado
    query_unauth = SimpleNamespace(
        data="ap:req-123",
        from_user=SimpleNamespace(id=999),
        message=SimpleNamespace(chat=SimpleNamespace(id=42)),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update_unauth = SimpleNamespace(callback_query=query_unauth, effective_user=SimpleNamespace(id=999))
    context = SimpleNamespace()
    asyncio.run(bot.approval_callback(update_unauth, context))
    query_unauth.answer.assert_awaited_with("Não tens autorização para esta ação.", show_alert=True)

    # 2. Utilizador autorizado mas não é o dono do pedido
    query_not_owner = SimpleNamespace(
        data="ap:req-123",
        from_user=SimpleNamespace(id=200),
        message=SimpleNamespace(chat=SimpleNamespace(id=42)),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update_not_owner = SimpleNamespace(callback_query=query_not_owner, effective_user=SimpleNamespace(id=200))
    asyncio.run(bot.approval_callback(update_not_owner, context))
    query_not_owner.answer.assert_awaited_with("Apenas o autor do pedido pode aprovar ou rejeitar.", show_alert=True)

    # 3. Dono legítimo aprova -> estado muda para APPROVED
    query_owner = SimpleNamespace(
        data="ap:req-123",
        from_user=SimpleNamespace(id=100),
        message=SimpleNamespace(chat=SimpleNamespace(id=42)),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update_owner = SimpleNamespace(callback_query=query_owner, effective_user=SimpleNamespace(id=100))
    monkeypatch.setattr(bot, "grafo", SimpleNamespace(aupdate_state=AsyncMock()))
    monkeypatch.setattr(bot, "_dispatch_workflow_task", lambda *args: None)
    asyncio.run(bot.approval_callback(update_owner, context))

    saved_run = store.get("req-123")
    assert saved_run.status == WorkflowStatus.APPROVED

    # 4. Idempotência: segundo clique rejeitado
    query_second = SimpleNamespace(
        data="ap:req-123",
        from_user=SimpleNamespace(id=100),
        message=SimpleNamespace(chat=SimpleNamespace(id=42)),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update_second = SimpleNamespace(callback_query=query_second, effective_user=SimpleNamespace(id=100))
    asyncio.run(bot.approval_callback(update_second, context))
    query_second.answer.assert_awaited_with("Esta ação já foi processada anteriormente.", show_alert=True)


def test_rejection_stores_active_thread_for_continuation(monkeypatch, tmp_path):
    store = WorkflowStore(str(tmp_path / "test2.db"))
    monkeypatch.setattr(bot, "workflow_store", store)
    monkeypatch.setenv("ALLOWED_USER_IDS", "100")

    run = WorkflowRun(
        request_id="req-999",
        workflow="auto",
        user_id="100",
        status=WorkflowStatus.APPROVAL_REQUIRED,
        metadata={"thread_id": "42:req-999"},
    )
    store.save(run)

    query = SimpleNamespace(
        data="rj:req-999",
        from_user=SimpleNamespace(id=100),
        message=SimpleNamespace(chat=SimpleNamespace(id=42)),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=100))
    monkeypatch.setattr(bot, "grafo", SimpleNamespace(aupdate_state=AsyncMock()))
    monkeypatch.setattr(bot, "_dispatch_workflow_task", lambda *args: None)
    asyncio.run(bot.approval_callback(update, SimpleNamespace()))

    assert bot._active_chat_threads.get("42") == "42:req-999"


def test_generic_error_message_with_short_request_id(monkeypatch):
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=42))

    monkeypatch.setattr(bot, "_stream_graph", AsyncMock(side_effect=RuntimeError("internal db crash details")))

    asyncio.run(
        bot._run_graph_events(
            update,
            context,
            {"messages": [HumanMessage(content="teste")]},
            "chat-42",
            "abcdef1234567890",
        )
    )

    sent_text = context.bot.send_message.await_args.kwargs["text"]
    assert "internal db crash details" not in sent_text
    assert "RuntimeError" not in sent_text
    assert "ref: abcdef12" in sent_text
