from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class WorkflowStatus(StrEnum):
    DRAFT = "draft"
    RESEARCHING = "researching"
    PRODUCING = "producing"
    REVIEWING = "reviewing"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class ResearchSource(BaseModel):
    title: str
    url: str
    excerpt: str = ""
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reliability: str = "unknown"
    claim_supported: str = ""
    domain: str = ""


class ArtifactRecord(BaseModel):
    filename: str
    path: str
    mime_type: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    approved: bool = False
    sha256: str = ""
    size_bytes: int = 0
    version: int = 1


class ReviewResult(BaseModel):
    approved: bool
    score: int = Field(ge=0, le=100)
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    verified_sources: list[ResearchSource] = Field(default_factory=list)


class TrainingActivity(BaseModel):
    title: str
    duration_minutes: int = Field(gt=0)
    objective: str
    instructions: str
    resources: list[str] = Field(default_factory=list)


class TrainingPackage(BaseModel):
    title: str
    audience: str
    duration_minutes: int = Field(gt=0)
    prerequisites: list[str] = Field(default_factory=list)
    learning_objectives: list[str] = Field(min_length=1)
    session_plan: list[TrainingActivity] = Field(min_length=1)
    activities: list[dict[str, Any]] = Field(default_factory=list)
    assessment: list[str] = Field(min_length=1)
    resources: list[str] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    artifacts_to_generate: list[str] = Field(default_factory=list)


class RequirementsContext(BaseModel):
    objective: str = ""
    audience: str = ""
    constraints: list[str] = Field(default_factory=list)


class WorkflowRun(BaseModel):
    request_id: str
    workflow: str
    user_id: str
    status: WorkflowStatus = WorkflowStatus.DRAFT
    iteration: int = 0
    objective: str = ""
    sources: list[ResearchSource] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    quality_report: ReviewResult | None = None
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
