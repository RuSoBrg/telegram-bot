import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from routing.alerts import notify_disabled, reset_alerts_for_tests, configure_alert_sender


def test_estado_lists_available_and_cooldown(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_AVAILABILITY_DB_PATH", str(tmp_path / "availability.db"))
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("ALLOWED_USER_IDS", "42")
    from routing.availability import AvailabilityStore
    import bot

    store = AvailabilityStore()
    store.mark_unavailable(
        "openai",
        "blocked",
        datetime.now(timezone.utc) + timedelta(hours=1),
        "auth",
        "redacted",
    )
    monkeypatch.setattr(
        bot,
        "load_catalog",
        lambda: {
            "engenharia": [
                SimpleNamespace(provider="openai", model="blocked", model_id="openai/blocked"),
                SimpleNamespace(provider="openai", model="ready", model_id="openai/ready"),
            ]
        },
    )
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=42),
    )

    asyncio.run(bot.estado(update, context))

    text = context.bot.send_message.await_args.kwargs["text"]
    assert "⏳ volta a" in text
    assert "✅ disponível" in text


def test_disabled_alert_is_sent_once():
    sender = AsyncMock()
    configure_alert_sender(sender)
    reset_alerts_for_tests()
    try:
        asyncio.run(notify_disabled("provider", "openai", "any", "auth"))
        asyncio.run(notify_disabled("provider", "openai", "other", "auth"))
        assert sender.await_count == 1
    finally:
        configure_alert_sender(None)
        reset_alerts_for_tests()


def test_periodic_cleanup_task_is_cancelable(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_AVAILABILITY_DB_PATH", str(tmp_path / "availability.db"))
    monkeypatch.setenv("DATABASE_URL", "")
    import bot

    async def run():
        task = asyncio.create_task(bot._cleanup_availability_periodically())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()

    asyncio.run(run())


def test_empty_allowlist_is_private_by_default(monkeypatch):
    monkeypatch.delenv("ALLOWED_USER_IDS", raising=False)
    monkeypatch.setenv("ALLOW_PUBLIC", "false")
    import bot

    update = SimpleNamespace(effective_user=SimpleNamespace(id=42))
    assert not bot._is_authorized(update)

    monkeypatch.setenv("ALLOW_PUBLIC", "true")
    assert bot._is_authorized(update)
