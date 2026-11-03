from __future__ import annotations

import asyncio

from agents.models import QualityFirstRouter, _quality_first_deployment


def _deployment(score: int, name: str) -> dict:
    return {
        "model_name": "test-model",
        "litellm_params": {"model": name},
        "model_info": {"quality_score": score},
    }


def test_quality_first_deployment_ignores_input_order() -> None:
    deployments = [_deployment(70, "slow-model"), _deployment(95, "best-model")]

    selected = _quality_first_deployment(deployments)

    assert selected is not None
    assert selected["litellm_params"]["model"] == "best-model"


def test_quality_first_deployment_returns_none_without_available_models() -> None:
    assert _quality_first_deployment([]) is None


def test_router_selection_uses_highest_quality_healthy_deployment() -> None:
    router = object.__new__(QualityFirstRouter)
    deployments = [_deployment(80, "fallback"), _deployment(100, "primary")]

    selected = asyncio.run(
        router._select_deployment_async(
            strategy="least-busy",
            selector=None,
            model="test-model",
            healthy_deployments=deployments,
            messages=None,
            input=None,
            request_kwargs=None,
        )
    )

    assert selected is not None
    assert selected["litellm_params"]["model"] == "primary"
