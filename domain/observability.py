import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger("ai_workspace.workflow")


def log_event(event: str, *, request_id: str = "", **fields: Any) -> None:
    """Emite eventos estruturados sem incluir prompts, chaves ou conteúdo sensível."""
    safe_fields = {
        key: value
        for key, value in fields.items()
        if key not in {"api_key", "token", "prompt", "content", "messages"}
    }
    logger.info(
        json.dumps(
            {
                "event": event,
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **safe_fields,
            },
            ensure_ascii=False,
            default=str,
        )
    )


@contextmanager
def timed_event(event: str, *, request_id: str = "", **fields: Any):
    started = time.monotonic()
    log_event(f"{event}_started", request_id=request_id, **fields)
    try:
        yield
    except Exception as error:
        log_event(
            f"{event}_failed",
            request_id=request_id,
            duration_ms=round((time.monotonic() - started) * 1000),
            error_type=type(error).__name__,
            **fields,
        )
        raise
    else:
        log_event(
            f"{event}_completed",
            request_id=request_id,
            duration_ms=round((time.monotonic() - started) * 1000),
            **fields,
        )
