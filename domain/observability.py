import json
import logging
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
