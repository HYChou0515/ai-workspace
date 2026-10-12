"""A value, or a function giving it on each use (docs/plan-model-settings.md P5).

A receiver that used to be handed a value built once from the boot settings
takes `Live[T]` instead and reads it with `now` where it is used — so the
composition root can hand it a function that follows the published settings,
and a test can still hand it the plain value.
"""

from __future__ import annotations

from collections.abc import Callable

type Live[T] = T | Callable[[], T]


def now[T](value: Live[T]) -> T:
    return value() if callable(value) else value  # ty: ignore[call-top-callable]
