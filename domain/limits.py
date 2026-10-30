import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeLimits:
    max_quality_iterations: int = 3
    max_research_results: int = 5
    max_generated_content_chars: int = 100_000
    max_media_seconds: int = 60
    tool_timeout_seconds: int = 600


def load_runtime_limits() -> RuntimeLimits:
    return RuntimeLimits(
        max_quality_iterations=int(os.getenv("MAX_QUALITY_ITERATIONS", "3")),
        max_research_results=int(os.getenv("MAX_RESEARCH_RESULTS", "5")),
        max_generated_content_chars=int(os.getenv("MAX_GENERATED_CONTENT_CHARS", "100000")),
        max_media_seconds=int(os.getenv("MAX_MEDIA_SECONDS", "60")),
        tool_timeout_seconds=int(os.getenv("TOOL_TIMEOUT_SECONDS", "600")),
    )
