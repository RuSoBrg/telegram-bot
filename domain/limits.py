from dataclasses import dataclass

from app.settings import get_settings


@dataclass(frozen=True)
class RuntimeLimits:
    max_steps: int = 5
    max_quality_iterations: int = 3
    max_research_results: int = 5
    max_generated_content_chars: int = 100_000
    max_media_seconds: int = 60
    tool_timeout_seconds: int = 600
    max_user_operations: int = 100
    quota_window_seconds: int = 86400


def load_runtime_limits() -> RuntimeLimits:
    settings = get_settings()
    return RuntimeLimits(
        max_steps=settings.max_steps,
        max_quality_iterations=settings.max_quality_iterations,
        max_research_results=settings.max_research_results,
        max_generated_content_chars=settings.max_generated_content_chars,
        max_media_seconds=settings.max_media_seconds,
        tool_timeout_seconds=settings.tool_timeout_seconds,
        max_user_operations=settings.max_user_operations,
        quota_window_seconds=settings.quota_window_seconds,
    )
