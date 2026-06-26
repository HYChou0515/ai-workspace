"""FallbackLlm / FallbackVlm — thin ILlm/IVlm adapters over the failover core.

They turn a resolved endpoint chain into a busy-aware streaming LLM: each
endpoint is lazily materialised into a real (here: fake) ILlm only when its turn
comes, so a cooling endpoint is never even built.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest

from workspace_app.factories import LlmEndpoint
from workspace_app.failover.cooldown import CooldownRegistry
from workspace_app.failover.llm import FallbackLlm, FallbackVlm
from workspace_app.kb.llm import ILlm
from workspace_app.kb.vlm.protocol import IVlm


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _ep(model: str, *, base_url: str | None = None) -> LlmEndpoint:
    return LlmEndpoint(
        model=model,
        base_url=base_url,
        api_key=None,
        reasoning_effort=None,
        ttft_s=5.0,
        idle_s=5.0,
        cooldown_s=30.0,
    )


class _FakeLlm(ILlm):
    def __init__(
        self, chunks: list[tuple[str, bool]] | None = None, *, error: Exception | None = None
    ) -> None:
        self._chunks = chunks or []
        self._error = error

    def stream(self, prompt: str) -> Iterator[tuple[str, bool]]:
        if self._error is not None:
            raise self._error
        yield from self._chunks


class _FakeVlm(IVlm):
    def __init__(
        self, chunks: list[tuple[str, bool]] | None = None, *, error: Exception | None = None
    ) -> None:
        self._chunks = chunks or []
        self._error = error

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        if self._error is not None:
            raise self._error
        yield from self._chunks


def test_fallback_llm_switches_to_next_endpoint_on_failure():
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    impls = {
        "busy": _FakeLlm(error=RuntimeError("500")),
        "spare": _FakeLlm([("answer", False)]),
    }
    switched: list[tuple[str, str]] = []
    llm = FallbackLlm(
        [_ep("busy"), _ep("spare")],
        reg,
        make_llm=lambda e: impls[e.model],
        on_switch=lambda label, exc: switched.append((label, str(exc))),
    )
    assert llm.collect("q") == "answer"
    assert switched == [("busy", "500")]
    assert reg.is_cooling(("busy", "")) is True


def test_fallback_llm_single_endpoint_streams_through():
    reg = CooldownRegistry(clock=_Clock())
    llm = FallbackLlm(
        [_ep("solo")], reg, make_llm=lambda e: _FakeLlm([("hi ", False), ("there", False)])
    )
    assert llm.collect("q") == "hi there"


def test_fallback_llm_does_not_build_a_cooling_endpoint():
    reg = CooldownRegistry(clock=_Clock())
    reg.mark(("busy", ""), 30.0)
    built: list[str] = []

    def make(e: LlmEndpoint) -> ILlm:
        built.append(e.model)
        return _FakeLlm([("x", False)])

    llm = FallbackLlm([_ep("busy"), _ep("spare")], reg, make_llm=make)
    assert llm.collect("q") == "x"
    assert built == ["spare"]  # 'busy' is cooling → never materialised


class _Transient(Exception):
    status_code = 503  # a busy/blip endpoint, like litellm's 503


class _BadRequest(Exception):
    status_code = 400  # a permanent error switching can't fix


def test_fallback_vlm_retries_a_transient_then_switches_and_passes_images():
    seen: list[Sequence[tuple[bytes, str]]] = []

    def make(e: LlmEndpoint) -> IVlm:
        if e.model == "busy":
            return _FakeVlm(error=_Transient())  # transient blip → retry, then switch

        class _Recorder(_FakeVlm):
            def stream(self, prompt, *, images):
                seen.append(images)
                return super().stream(prompt, images=images)

        return _Recorder([("described", False)])

    # #249: the WHOLE describe is one retryable unit, yielded as a single chunk.
    switched: list[str] = []
    vlm = FallbackVlm(
        [_ep("busy"), _ep("spare")],
        make_vlm=make,
        on_switch=lambda label, exc: switched.append(label),
        sleep=lambda _s: None,
    )
    out = vlm.collect("describe", images=[(b"img", "image/png")])
    assert out == "described"  # switched to the spare after the busy endpoint blipped
    assert seen == [[(b"img", "image/png")]]  # images rode along to the winning endpoint
    assert switched == ["busy"]  # the degradation notice fired for the blipping endpoint


def test_fallback_vlm_switch_without_an_on_switch_hook_is_silent():
    def make(e: LlmEndpoint) -> IVlm:
        return _FakeVlm(error=_Transient()) if e.model == "busy" else _FakeVlm([("ok", False)])

    vlm = FallbackVlm([_ep("busy"), _ep("spare")], make_vlm=make, sleep=lambda _s: None)
    assert vlm.collect("d", images=[]) == "ok"  # no on_switch wired → the switch is silent


def test_fallback_vlm_aborts_on_a_permanent_error_without_switching():
    built: list[str] = []

    def make(e: LlmEndpoint) -> IVlm:
        built.append(e.model)
        return _FakeVlm(error=_BadRequest())  # a 400 can't be fixed by switching

    vlm = FallbackVlm([_ep("first"), _ep("spare")], make_vlm=make, sleep=lambda _s: None)
    with pytest.raises(_BadRequest):
        vlm.collect("describe", images=[(b"img", "image/png")])
    assert built == ["first"]  # the spare is never even built — a bad request is terminal
