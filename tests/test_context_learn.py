"""#624 P3: learn the real ceiling from the traffic, so silent truncation shows.

A provider that truncates instead of erroring (Ollama, measured: it fed the
model 3,983 of 8,755 tokens and the model then confidently invented an answer)
tells us nothing — unless we compare what we sent against the `prompt_tokens`
it reports back. That instrument was already being collected and thrown away.
"""

from __future__ import annotations

from workspace_app.context_budget import LimitLearner, detect_truncation

# ── the observation ─────────────────────────────────────────────────


def test_a_much_smaller_reported_prompt_means_truncation():
    """We sent ~20k, the provider says it read 4,091 — it silently dropped the
    front. That number IS its effective window."""
    assert detect_truncation(sent_estimate=20_000, reported_prompt_tokens=4_091) == 4_091


def test_a_close_match_is_not_truncation():
    """The estimator runs ~15% off; that must never be read as a cut."""
    assert detect_truncation(sent_estimate=8_000, reported_prompt_tokens=8_755) is None
    assert detect_truncation(sent_estimate=8_755, reported_prompt_tokens=8_000) is None


def test_a_reported_count_larger_than_our_estimate_is_not_truncation():
    """Under-estimating is our own bug, not the provider cutting anything."""
    assert detect_truncation(sent_estimate=2_435, reported_prompt_tokens=8_755) is None


def test_absent_usage_is_not_evidence():
    """Ollama often streams usage as 0 / absent — silence is not a measurement."""
    assert detect_truncation(sent_estimate=20_000, reported_prompt_tokens=None) is None
    assert detect_truncation(sent_estimate=20_000, reported_prompt_tokens=0) is None


def test_a_tiny_turn_cannot_trigger_a_false_positive():
    """Short prompts legitimately report small counts. Below the floor we make
    no claim — a wrong "limit" learned here would trim every later turn."""
    assert detect_truncation(sent_estimate=300, reported_prompt_tokens=100) is None


# ── the learner ─────────────────────────────────────────────────────


def test_the_learner_starts_empty():
    assert LimitLearner().get("qwen3", "http://x") is None


def test_one_observation_is_not_enough_to_start_trimming():
    """A single odd reading must not become policy — the cost of a wrong limit
    is trimming a user's memory on every subsequent turn."""
    learner = LimitLearner(confirmations=2)
    learner.observe("qwen3", "http://x", limit=4_091)
    assert learner.get("qwen3", "http://x") is None


def test_a_repeated_observation_is_learned():
    learner = LimitLearner(confirmations=2)
    learner.observe("qwen3", "http://x", limit=4_091)
    learner.observe("qwen3", "http://x", limit=4_090)  # same ceiling, ±noise
    assert learner.get("qwen3", "http://x") == 4_090


def test_learning_is_keyed_per_endpoint():
    """One deploy can front several models/endpoints; a ceiling learned for one
    must not govern another."""
    learner = LimitLearner(confirmations=1)
    learner.observe("qwen3", "http://a", limit=4_096)
    assert learner.get("qwen3", "http://b") is None
    assert learner.get("other", "http://a") is None


def test_a_rejection_teaches_immediately():
    """A 400 states the ceiling outright — no confirmation needed, it is not an
    inference."""
    learner = LimitLearner(confirmations=2)
    learner.learn_exact("qwen3", "http://x", limit=32_768)
    assert learner.get("qwen3", "http://x") == 32_768


def test_a_later_observation_can_overrule_a_learned_value():
    """Models get swapped behind an endpoint; a learned value is a cache, never
    a permanent truth."""
    learner = LimitLearner(confirmations=1)
    learner.learn_exact("qwen3", "http://x", limit=32_768)
    learner.learn_exact("qwen3", "http://x", limit=8_192)
    assert learner.get("qwen3", "http://x") == 8_192


# ── the wiring (adversarial review proved this was all dead code) ────


async def _drain(runner, ctx):
    async for _ in runner.run("go", ctx):
        pass


def _ctx():
    from workspace_app.agent.context import AgentToolContext
    from workspace_app.resources import AgentConfig

    return AgentToolContext(
        investigation_id="i",
        agent_config=AgentConfig(name="t", model="m", system_prompt="s"),
    )


async def test_a_silently_truncated_turn_is_detected_and_learned():
    """The Ollama case, which is the dev default: no error, no warning — the
    model reads the tail and answers confidently. The only signal is that the
    provider reports having read far less than we sent."""
    from workspace_app.api.events import MessageDelta
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    class _Runner(LitellmAgentRunner):
        async def _run_once(self, prompt, ctx, feedback):  # type: ignore[override]
            yield MessageDelta(text="ok")
            self._note_prompt_usage(ctx, sent_estimate=20_000, reported=4_091)

    runner = _Runner()
    await _drain(runner, _ctx())
    assert runner.learned_limit("m", None) is None, "one sighting is an inference"
    await _drain(runner, _ctx())
    assert runner.learned_limit("m", None) == 4_091, "a second confirms it"


async def test_a_healthy_turn_teaches_nothing():
    """A provider that read what we sent must never be mistaken for one that
    truncated — a wrong ceiling would trim every later turn."""
    from workspace_app.api.events import MessageDelta
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    class _Runner(LitellmAgentRunner):
        async def _run_once(self, prompt, ctx, feedback):  # type: ignore[override]
            yield MessageDelta(text="ok")
            self._note_prompt_usage(ctx, sent_estimate=8_000, reported=8_755)

    runner = _Runner()
    for _ in range(3):
        await _drain(runner, _ctx())
    assert runner.learned_limit("m", None) is None


# ── what a proxy in front of the model was told ──────────────────────────


def test_an_unset_per_config_endpoint_falls_back_to_the_deploys_own():
    """The bug this rung was shipped with, pinned.

    `AgentConfig.llm_base_url == ""` MEANS "use the deploy's endpoint" — that is
    the documented contract, and it is the normal shape for a deployment with
    one endpoint whose presets name only a model. Every other consumer of that
    field does this fallback. The first version of this rung did not, so it saw
    no endpoint, asked nothing, and silently changed nothing on precisely the
    deployment it was written for."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    seen: list[str | None] = []
    runner = LitellmAgentRunner(base_url="http://the-deploys-proxy/v1")
    runner._declared_probe = lambda base_url, model: seen.append(base_url)

    runner.endpoint_limits("our-alias", None)
    assert seen == ["http://the-deploys-proxy/v1"]


def test_a_per_config_endpoint_still_wins():
    """A preset that names its own endpoint is asked about THAT one — the
    fallback is for the empty case, not a override."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    seen: list[str | None] = []
    runner = LitellmAgentRunner(base_url="http://the-deploys-proxy/v1")
    runner._declared_probe = lambda base_url, model: seen.append(base_url)

    runner.endpoint_limits("our-alias", "http://a-different-one/v1")
    assert seen == ["http://a-different-one/v1"]


def test_the_proxy_is_asked_once_per_endpoint_not_once_per_turn():
    """An HTTP round trip reached from inside a turn. Asking per turn would put
    one in front of every message for a value that does not change — and the
    silence has to be cached too, because most endpoints are not a litellm proxy
    and would otherwise be re-asked forever."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    calls: list[tuple[str | None, str]] = []

    def _once(base_url: str | None, model: str) -> None:
        calls.append((base_url, model))
        return None  # the ordinary answer: not a litellm proxy

    runner = LitellmAgentRunner(base_url="http://proxy/v1")
    runner._declared_probe = _once

    for _ in range(3):
        runner.endpoint_limits("our-alias", None)
    assert len(calls) == 1


def test_two_endpoints_under_one_model_name_are_asked_separately():
    """The answer belongs to the endpoint, not to the model name — two presets
    can carry the same name across different proxies."""
    from workspace_app.api.litellm_runner import LitellmAgentRunner

    calls: list[str | None] = []
    runner = LitellmAgentRunner(base_url="http://a/v1")
    runner._declared_probe = lambda base_url, model: calls.append(base_url)

    runner.endpoint_limits("same-name", "http://a/v1")
    runner.endpoint_limits("same-name", "http://b/v1")
    assert calls == ["http://a/v1", "http://b/v1"]


def test_a_negative_from_the_proxy_is_asked_again_later():
    """The one cached value here that a HUMAN is expected to change.

    The proxy's model list is a config file on ANOTHER service, and the
    documented next step for a deployment sitting on `unknown` is that somebody
    adds `max_input_tokens` to it. Remembering "it did not say" for the life of
    the pod means their edit does nothing until WE are redeployed — a silent
    dependency between two services that nothing would have told either of them
    about. It is also what a probe that landed during a proxy restart leaves
    behind: a legitimate-looking negative, permanently."""
    from workspace_app.api.litellm_runner import (
        DECLARED_RETRY_S,
        LitellmAgentRunner,
    )

    now = [0.0]
    calls: list[str | None] = []
    runner = LitellmAgentRunner(base_url="http://proxy/v1")
    runner._clock = lambda: now[0]
    runner._declared_probe = lambda base_url, model: calls.append(base_url)

    runner.endpoint_limits("our-alias", None)
    now[0] = DECLARED_RETRY_S - 1
    runner.endpoint_limits("our-alias", None)
    assert len(calls) == 1, "not once per turn — that is a round trip per message"

    now[0] = DECLARED_RETRY_S + 1
    runner.endpoint_limits("our-alias", None)
    assert len(calls) == 2, "but eventually, or the operator's fix needs a redeploy of us"


def test_an_answer_is_kept_for_the_life_of_the_pod():
    """Only the SILENCE expires. A number that was stated changes when the proxy
    is reconfigured, and re-asking for it would spend a round trip to relearn
    what we already know."""
    from workspace_app.api.litellm_runner import DECLARED_RETRY_S, LitellmAgentRunner
    from workspace_app.context_probe import EndpointLimits

    now = [0.0]
    calls: list[str | None] = []

    def _answers(base_url: str | None, model: str) -> EndpointLimits:
        calls.append(base_url)
        return EndpointLimits(max_input_tokens=131072, max_tokens=8192)

    runner = LitellmAgentRunner(base_url="http://proxy/v1")
    runner._clock = lambda: now[0]
    runner._declared_probe = _answers

    assert runner.endpoint_limits("our-alias", None).max_input_tokens == 131072
    now[0] = DECLARED_RETRY_S * 10
    assert runner.endpoint_limits("our-alias", None).max_input_tokens == 131072
    assert len(calls) == 1
