from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

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


def test_quality_order_metadata_enables_intra_pool_fallback() -> None:
    deployments = [
        {**_deployment(100, "primary"), "model_info": {"quality_score": 100, "order": 1}},
        {**_deployment(90, "fallback"), "model_info": {"quality_score": 90, "order": 2}},
    ]

    assert deployments[0]["model_info"]["order"] < deployments[1]["model_info"]["order"]


def test_router_logs_next_deployment_on_failure(caplog) -> None:
    router = object.__new__(QualityFirstRouter)
    deployments = [
        _deployment(100, "primary"),
        _deployment(90, "fallback"),
    ]
    router.get_model_list = lambda model_name: deployments
    for index, deployment in enumerate(deployments, start=1):
        deployment["model_info"]["id"] = str(index)
        deployment["model_info"]["order"] = index

    with caplog.at_level(logging.WARNING):
        with patch(
            "litellm.Router.deployment_callback_on_failure", return_value=True
        ):
            QualityFirstRouter.deployment_callback_on_failure(
                router,
                {
                    "model": "test-model",
                    "litellm_params": {"model": "primary"},
                    "model_info": {"id": "1"},
                },
                None,
                None,
                None,
            )

    assert "A iniciar fallback para fallback" in caplog.text
