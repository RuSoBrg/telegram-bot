import hashlib
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class ArtifactMetadata(TypedDict, total=False):
    artifact_id: str
    type: str
    name: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str


class SourceMetadata(TypedDict, total=False):
    source_id: str
    title: str
    url: str
    domain: str
    reliability: str


def compact_source_metadata(source: dict[str, Any]) -> SourceMetadata:
    url = str(source.get("url", ""))
    return {
        "source_id": hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
        "title": str(source.get("titulo") or source.get("title") or "")[:300],
        "url": url,
        "domain": str(source.get("domain", "")),
        "reliability": str(source.get("reliability", "unknown")),
    }


class WorkflowState(TypedDict, total=False):
    request_id: str
    user_id: str
    objective: str
    audience: str
    constraints: list[str]
    plan: list[dict]
    current_plan_step: int
    completed_plan_steps: list[int]
    messages: Annotated[list, add_messages]
    research_sources: list[SourceMetadata]
    artifacts: list[ArtifactMetadata]
    quality_report: dict | None
    iteration: int
    approval_required: bool
    status: str
    errors: list[str]
    instruction: str
    quality_feedback: str
    quality_iterations: int
    steps: int
    approval_decision: str
    agent_used: str
    model_used: str
