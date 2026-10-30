from domain.models import ReviewResult


def build_review_result(
    *,
    approved: bool,
    score: int,
    blocking_issues: list[str] | None = None,
    warnings: list[str] | None = None,
    required_changes: list[str] | None = None,
    verified_sources: list[dict] | None = None,
) -> ReviewResult:
    """Aplica o quality gate comum a todos os supervisores."""
    issues = list(blocking_issues or [])
    return ReviewResult(
        approved=approved and score >= 85 and not issues,
        score=score,
        blocking_issues=issues,
        warnings=warnings or [],
        required_changes=required_changes or [],
        verified_sources=verified_sources or [],
    )
