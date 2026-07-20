"""A turn must always end.

The ttft / idle deadlines used to exist ONLY inside ``FallbackModel``, which is
built only when a preset declares two or more ``fallbacks`` — and ``fallbacks:``
is commented out in ``config.example.yaml``. So on the default single-endpoint
setup nothing bounded the LLM call: if the provider accepted the request and then
went silent, the turn waited forever, emitted no event, persisted nothing, and no
watchdog existed. The user saw a spinner and a counter climbing with no way to
learn why.

``DeadlineModel`` applies the same two deadlines to any model, independently of
whether failover is configured.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from agents.models.interface import Model

from workspace_app.agent.deadline_model import DeadlineModel
from workspace_app.failover.core import StreamStalled, TtftTimeout


class _Stream(Model):
    """A model whose stream yields the given items, sleeping before each."""

    def __init__(self, plan: list[tuple[float, str]]) -> None:
        self.plan = plan
        self.closed = False

    async def stream_response(self, *_a: Any, **_k: Any):
        try:
            for delay, item in self.plan:
                await asyncio.sleep(delay)
                yield item
        finally:
            # Records that the generator was closed (aclose) rather than merely
            # abandoned — an abandoned one leaks the provider connection.
            self.closed = True

    async def get_response(self, *_a: Any, **_k: Any) -> Any:
        return "unused"


async def _drain(model: Any) -> list[str]:
    return [ev async for ev in model.stream_response()]


@pytest.mark.asyncio
async def test_passes_a_healthy_stream_through_untouched() -> None:
    model = DeadlineModel(_Stream([(0.0, "a"), (0.0, "b")]), ttft_s=1.0, idle_s=1.0)
    assert await _drain(model) == ["a", "b"]


@pytest.mark.asyncio
async def test_a_provider_that_never_starts_raises_instead_of_hanging() -> None:
    model = DeadlineModel(_Stream([(10.0, "never")]), ttft_s=0.05, idle_s=10.0)
    with pytest.raises(TtftTimeout):
        await _drain(model)


@pytest.mark.asyncio
async def test_a_stream_that_stalls_mid_answer_raises() -> None:
    model = DeadlineModel(_Stream([(0.0, "a"), (10.0, "b")]), ttft_s=1.0, idle_s=0.05)
    with pytest.raises(StreamStalled):
        await _drain(model)


@pytest.mark.asyncio
async def test_an_empty_stream_is_a_valid_finish_not_a_timeout() -> None:
    model = DeadlineModel(_Stream([]), ttft_s=0.05, idle_s=0.05)
    assert await _drain(model) == []


@pytest.mark.asyncio
async def test_a_non_positive_deadline_disables_that_bound() -> None:
    # An operator can opt out (0 = no bound) without the wrapper changing shape.
    model = DeadlineModel(_Stream([(0.1, "a")]), ttft_s=0.0, idle_s=0.0)
    assert await _drain(model) == ["a"]


@pytest.mark.asyncio
async def test_the_abandoned_inner_stream_is_closed_on_timeout() -> None:
    # Leaving the inner generator suspended leaks the provider connection.
    inner = _Stream([(10.0, "never")])
    model = DeadlineModel(inner, ttft_s=0.05, idle_s=1.0)
    with pytest.raises(TtftTimeout):
        await _drain(model)
    assert inner.closed is True


@pytest.mark.asyncio
async def test_non_streaming_calls_are_delegated_unchanged() -> None:
    model = DeadlineModel(_Stream([]), ttft_s=1.0, idle_s=1.0)
    assert await model.get_response() == "unused"
