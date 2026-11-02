"""Shared execution safety for LangChain tools."""

from __future__ import annotations

import inspect
import logging
from functools import wraps
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar, cast, overload

P = ParamSpec("P")
R = TypeVar("R")
logger = logging.getLogger(__name__)


@overload
def safe_tool_execution(
    function: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[str]]:
    ...


@overload
def safe_tool_execution(function: Callable[P, R]) -> Callable[P, str]:
    ...


def safe_tool_execution(function: Callable[..., Any]) -> Callable[..., Any]:
    """Convert tool failures into model-readable strings without raising."""

    if inspect.iscoroutinefunction(function):

        @wraps(function)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> str:
            try:
                result = await cast(Callable[P, Awaitable[R]], function)(
                    *args, **kwargs
                )
                return str(result)
            except Exception as error:
                logger.exception("Tool %s failed", function.__name__)
                return f"Error executing tool {function.__name__}: {error}"

        return async_wrapper

    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> str:
        try:
            result = cast(Callable[P, R], function)(*args, **kwargs)
            return str(result)
        except Exception as error:
            logger.exception("Tool %s failed", function.__name__)
            return f"Error executing tool {function.__name__}: {error}"

    return wrapper
