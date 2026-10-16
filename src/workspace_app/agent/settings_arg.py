"""Where a model call's `ModelSettings` travels, for the wrappers that change it.

The SDK's run loop does not agree with itself about how it passes them:
``stream_response`` sends ``model_settings`` POSITIONALLY (the 3rd argument,
after ``system_instructions`` and ``input``), ``get_response`` sends it by
KEYWORD. A wrapper that reads only one shape misses the other in silence — a
credential header that never reaches the wire, or (#915 review round 3) an
``IndexError`` on every non-streamed turn. Both wrappers that rewrite the
settings go through this one function so they cannot drift apart."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents import ModelSettings


def with_settings(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    change: Callable[[ModelSettings], ModelSettings],
    *,
    at: int,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """``(args, kwargs)`` with the call's settings replaced by ``change(settings)``.

    ``at`` is the settings' position in ``args``: 2 for a wrapper taking
    ``*args`` whole, 0 for one that names ``system_instructions`` and ``input``."""
    if "model_settings" in kwargs:
        return args, {**kwargs, "model_settings": change(kwargs["model_settings"])}
    if len(args) > at and isinstance(args[at], ModelSettings):
        return (*args[:at], change(args[at]), *args[at + 1 :]), kwargs
    return args, kwargs
