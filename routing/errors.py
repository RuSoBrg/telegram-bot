from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

import litellm


class ErrorKind(StrEnum):
    RATE_LIMIT_MINUTE = "rate_limit_minute"
    QUOTA_DAILY = "quota_daily"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    AUTH = "auth"
    MODEL_NOT_FOUND = "model_not_found"
    CONTEXT_TOO_LONG = "context_too_long"
    STRUCTURED_OUTPUT_FAILURE = "structured_output_failure"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassifiedError:
    kind: ErrorKind
    retry_at: datetime | None
    disable: str | None


def _headers(exc: Exception) -> dict[str, Any]:
    for source in (exc, getattr(exc, "response", None)):
        headers = getattr(source, "headers", None)
        if headers:
            return {str(key).lower(): value for key, value in dict(headers).items()}
    return {}


def _retry_after(exc: Exception) -> int | None:
    value = _headers(exc).get("retry-after") or getattr(exc, "retry_after", None)
    if value is None:
        return None
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return None


def classify_error(
    exc: Exception,
    *,
    fail_count: int = 0,
    structured: bool = False,
) -> ClassifiedError:
    now = datetime.now(timezone.utc)
    text = str(exc).lower()
    if structured:
        return ClassifiedError(ErrorKind.STRUCTURED_OUTPUT_FAILURE, None, None)
    if isinstance(exc, litellm.ContextWindowExceededError) or "context" in text and "long" in text:
        return ClassifiedError(ErrorKind.CONTEXT_TOO_LONG, None, None)
    if isinstance(exc, litellm.AuthenticationError):
        return ClassifiedError(ErrorKind.AUTH, None, "provider")
    if isinstance(exc, litellm.NotFoundError) or "model not found" in text:
        return ClassifiedError(ErrorKind.MODEL_NOT_FOUND, None, "model")
    if isinstance(exc, litellm.RateLimitError):
        daily = any(word in text for word in ("daily", "quota", "limit reached", "resets"))
        seconds = _retry_after(exc)
        if daily and seconds is None:
            reset = _headers(exc).get("x-ratelimit-reset")
            try:
                seconds = max(1, int(float(reset)) - int(now.timestamp()))
            except (TypeError, ValueError):
                tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                seconds = int((tomorrow - now).total_seconds())
        seconds = seconds or 60
        kind = ErrorKind.QUOTA_DAILY if daily else ErrorKind.RATE_LIMIT_MINUTE
        return ClassifiedError(kind, now + timedelta(seconds=seconds), None)
    if isinstance(exc, (litellm.Timeout, TimeoutError)):
        seconds = (1, 2, 5, 15)[min(fail_count, 3)] * 60
        return ClassifiedError(ErrorKind.TIMEOUT, now + timedelta(seconds=seconds), None)
    if isinstance(exc, litellm.ServiceUnavailableError) or "server error" in text:
        seconds = (1, 2, 5, 15)[min(fail_count, 3)] * 60
        return ClassifiedError(ErrorKind.SERVER_ERROR, now + timedelta(seconds=seconds), None)
    return ClassifiedError(ErrorKind.UNKNOWN, now + timedelta(minutes=1), None)
