"""#624 P5: ask the endpoint, and survive every way it can decline to answer.

vLLM exposes `/tokenize`, which returns both an exact token count and
`max_model_len`. That is the only source that knows the truth for a self-hosted
model — no registry has its name, and both local estimators disagree with the
real tokenizer (measured on one Chinese string: ours 33, litellm's 58, real
~30-34).

But `/tokenize` is a vLLM extension, not part of the OpenAI-compatible spec —
proven by Ollama, whose `/v1/models` carries no length field at all. So the
probe's most-exercised path is the one where it gets nothing back, and that path
must be silent, cheap, and non-fatal: the whole P1-P4 design works without it.
"""

from __future__ import annotations

import json

import pytest

from workspace_app.context_probe import probe_context_limit


class _Resp:
    def __init__(self, status: int, body: object) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    @property
    def text(self) -> str:
        return json.dumps(self._body) if not isinstance(self._body, Exception) else ""


def _client(resp: object):
    """A minimal stand-in for the HTTP client: returns `resp`, or raises it."""

    def post(url: str, **kw: object) -> _Resp:
        if isinstance(resp, Exception):
            raise resp
        assert isinstance(resp, _Resp)
        return resp

    return type("C", (), {"post": staticmethod(post)})()


def test_a_vllm_answer_is_used():
    """The documented shape: `/tokenize` reports the model's own ceiling."""
    got = probe_context_limit(
        base_url="http://vllm",
        model="qwen3-14b",
        client=_client(_Resp(200, {"count": 6, "max_model_len": 32768})),
    )
    assert got == 32768


@pytest.mark.parametrize(
    "resp",
    [
        _Resp(404, {"detail": "Not Found"}),  # Ollama / any non-vLLM server
        _Resp(200, {"count": 6}),  # answered, but no ceiling in it
        _Resp(200, {"max_model_len": 0}),  # nonsense value
        _Resp(200, ["not", "a", "dict"]),  # unexpected shape
        _Resp(200, ValueError("not json")),  # unparseable body
        _Resp(500, {"detail": "boom"}),  # server error
        ConnectionError("refused"),  # nothing listening
        TimeoutError("slow"),  # hung endpoint
    ],
)
def test_every_way_of_getting_nothing_yields_none(resp):
    """This is the path #624's design is built around — `None` here is normal,
    not a failure, and must never raise: P1-P4 carry the whole feature without
    a working probe."""
    assert probe_context_limit(base_url="http://x", model="m", client=_client(resp)) is None


def test_no_base_url_is_not_probed():
    """Nothing to ask — don't invent a request."""
    assert probe_context_limit(base_url="", model="m", client=_client(_Resp(200, {}))) is None
    assert probe_context_limit(base_url=None, model="m", client=_client(_Resp(200, {}))) is None


# ── the wiring (adversarial review: the probe had no caller) ─────────


def test_the_runner_consults_the_probe_for_an_unknown_endpoint():
    """A probe nobody calls is documentation, not a feature. When the endpoint
    answers, that number must reach the ladder — it is the only source that
    knows the truth for a self-hosted model no registry has heard of."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    runner = LitellmAgentRunner(base_url="http://vllm")
    runner._probe = lambda base_url, model: 32768  # the endpoint answered

    assert runner.learned_limit("self-hosted-qwen", "http://vllm") == 32768


def test_a_silent_probe_leaves_the_ladder_untouched():
    """404 / timeout / not-vLLM is the NORMAL path — it must add nothing, not
    poison the ladder with a zero or a guess."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    runner = LitellmAgentRunner(base_url="http://ollama")
    runner._probe = lambda base_url, model: None

    assert runner.learned_limit("m", "http://ollama") is None


def test_the_probe_is_asked_once_per_endpoint():
    """It is a startup nicety, not a per-turn dependency — asking every turn
    would add a round-trip to each message for a value that does not change."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    calls: list[str] = []
    runner = LitellmAgentRunner(base_url="http://vllm")

    def _probe(base_url, model):
        calls.append(model)
        return 8192

    runner._probe = _probe
    for _ in range(3):
        runner.learned_limit("m", "http://vllm")
    assert calls == ["m"], "the answer must be cached, not re-asked"


async def test_the_probe_never_runs_on_the_event_loop():
    """The probe is a SYNCHRONOUS http.post with a 3s timeout, and the turn
    context that reads it is built inside `async def build_chat_turn`. So an
    endpoint that hangs would stall the whole pod's event loop for three
    seconds on its first turn — every unrelated request stalling with it. That
    is the same defect class as the 254 ms estimator stall the adversarial
    review already removed, only twelve times worse.

    Nothing has to be invented to fix it: `unknown ⇒ do not trim` is the locked
    default (§3), so the first turn behaves exactly as designed while the probe
    runs elsewhere.
    """
    import asyncio
    import time

    from workspace_app.api.litellm_runner import LitellmAgentRunner

    runner = LitellmAgentRunner()
    started = asyncio.Event()

    def _slow_probe(base_url, model):
        started.set()
        time.sleep(0.5)  # a hanging endpoint, scaled down
        return 32_768

    runner._probe = _slow_probe

    loop_free = time.perf_counter()
    got = await asyncio.to_thread(lambda: None) or runner.learned_limit("m", "http://vllm")
    elapsed = time.perf_counter() - loop_free

    assert elapsed < 0.2, f"the probe blocked the event loop for {elapsed:.2f}s"
    assert got is None, "an endpoint we have not heard from yet is `unknown`, not a guess"


async def test_a_backgrounded_probe_still_teaches_the_next_turn():
    """Off the request path, but not thrown away — the whole point of asking."""
    import asyncio

    from workspace_app.api.litellm_runner import LitellmAgentRunner

    runner = LitellmAgentRunner()
    runner._probe = lambda base_url, model: 32_768

    assert runner.learned_limit("m", "http://vllm") is None  # first turn: not back yet
    for _ in range(50):
        await asyncio.sleep(0.01)
        if runner.learned_limit("m", "http://vllm") is not None:
            break

    assert runner.learned_limit("m", "http://vllm") == 32_768
