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
    model = DeadlineModel(_Stream([(0.0, "a"), (0.0, "b")]), first_event_s=1.0, idle_s=1.0)
    assert await _drain(model) == ["a", "b"]


@pytest.mark.asyncio
async def test_a_provider_that_never_starts_raises_instead_of_hanging() -> None:
    model = DeadlineModel(_Stream([(10.0, "never")]), first_event_s=0.05, idle_s=10.0)
    with pytest.raises(TtftTimeout):
        await _drain(model)


@pytest.mark.asyncio
async def test_a_stream_that_stalls_mid_answer_raises() -> None:
    model = DeadlineModel(_Stream([(0.0, "a"), (10.0, "b")]), first_event_s=1.0, idle_s=0.05)
    with pytest.raises(StreamStalled):
        await _drain(model)


@pytest.mark.asyncio
async def test_an_empty_stream_is_a_valid_finish_not_a_timeout() -> None:
    model = DeadlineModel(_Stream([]), first_event_s=0.05, idle_s=0.05)
    assert await _drain(model) == []


@pytest.mark.asyncio
async def test_a_non_positive_deadline_disables_that_bound() -> None:
    # An operator can opt out (0 = no bound) without the wrapper changing shape.
    model = DeadlineModel(_Stream([(0.1, "a")]), first_event_s=0.0, idle_s=0.0)
    assert await _drain(model) == ["a"]


@pytest.mark.asyncio
async def test_the_abandoned_inner_stream_is_closed_on_timeout() -> None:
    # Leaving the inner generator suspended leaks the provider connection.
    inner = _Stream([(10.0, "never")])
    model = DeadlineModel(inner, first_event_s=0.05, idle_s=1.0)
    with pytest.raises(TtftTimeout):
        await _drain(model)
    assert inner.closed is True


@pytest.mark.asyncio
async def test_non_streaming_calls_are_delegated_unchanged() -> None:
    model = DeadlineModel(_Stream([]), first_event_s=1.0, idle_s=1.0)
    assert await model.get_response() == "unused"


def test_the_single_endpoint_bound_is_the_give_up_budget_not_the_switch_signal() -> None:
    """A regression guard on WHICH number gets wired, not on the wrapper.

    ``ttft_timeout_s`` (8s) means "this endpoint is busy, try the next one" — a
    switching signal that is cheap precisely because there IS a next one. On a
    single endpoint there is nowhere to switch, so wiring it here would only kill
    turns for being slow (this deploy measures a 14.7s median, a 28.5s p90 and an
    83.8s worst case, and a long blank window is usually the provider being busy
    rather than dead).

    The bound that must always exist is the give-up budget, ``total_deadline_s``,
    whose own docstring is "caps the whole turn so it fails readably instead of
    hanging forever". Swapping one for the other silently converts "we gave up
    and told you" into "we killed a working turn".
    """
    from workspace_app.config.schema import Settings
    from workspace_app.factories import get_runner

    settings = Settings()
    runner = get_runner(settings)

    first_event_s, idle_s = runner._stream_deadlines  # ty: ignore[unresolved-attribute]
    assert first_event_s == settings.failover.total_deadline_s
    assert first_event_s != settings.failover.ttft_timeout_s
    assert idle_s == settings.failover.idle_timeout_s


@pytest.mark.asyncio
async def test_unrelated_attributes_reach_the_wrapped_model() -> None:
    """The wrapper is transparent for everything that isn't streaming.

    The SDK reads incidental attributes off the model it was handed, so a wrapper
    that swallowed them would break the turn in a way none of the streaming tests
    above would notice. ``RepairingModel`` forwards for the same reason.
    """
    inner = _Stream([])
    inner.tracing_label = "ollama/qwen"  # ty: ignore[unresolved-attribute]
    model = DeadlineModel(inner, first_event_s=1.0, idle_s=1.0)

    assert model.tracing_label == "ollama/qwen"
    with pytest.raises(AttributeError):
        _ = model.no_such_attribute
