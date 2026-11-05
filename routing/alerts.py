from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

AlertSender = Callable[[str, str], Awaitable[object]]
_sender: AlertSender | None = None
_sent_events: set[tuple[str, str, str]] = set()


def configure_alert_sender(sender: AlertSender | None) -> None:
    global _sender
    _sender = sender


async def notify_disabled(
    target_type: str,
    provider: str,
    model: str,
    reason: str,
) -> None:
    event = (target_type, provider, model if target_type == "model" else "")
    if event in _sent_events:
        return
    _sent_events.add(event)
    if _sender is None:
        return
    try:
        await _sender(
            f"⚠️ Routing desativou {target_type} {provider}/{model} "
            f"(motivo: {reason}).",
            "routing-alert",
        )
    except Exception:
        logger.exception("Não foi possível enviar alerta de routing ao administrador.")


def reset_alerts_for_tests() -> None:
    _sent_events.clear()
