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
