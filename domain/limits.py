import os
from dataclasses import dataclass


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
    return RuntimeLimits(
        max_steps=int(os.getenv("MAX_STEPS", "5")),
        max_quality_iterations=int(os.getenv("MAX_QUALITY_ITERATIONS", "3")),
        max_research_results=int(os.getenv("MAX_RESEARCH_RESULTS", "5")),
        max_generated_content_chars=int(os.getenv("MAX_GENERATED_CONTENT_CHARS", "100000")),
        max_media_seconds=int(os.getenv("MAX_MEDIA_SECONDS", "60")),
        tool_timeout_seconds=int(os.getenv("TOOL_TIMEOUT_SECONDS", "600")),
        max_user_operations=int(os.getenv("MAX_USER_OPERATIONS", "100")),
        quota_window_seconds=int(os.getenv("QUOTA_WINDOW_SECONDS", "86400")),
    )
