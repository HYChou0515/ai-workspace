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
