from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

from domain.models import ReviewResult


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
    research_sources: list[dict]
    artifacts: list[dict]
    quality_report: ReviewResult | None
    iteration: int
    approval_required: bool
    status: str
    errors: list[str]
    instruction: str
    quality_feedback: str
    quality_iterations: int
