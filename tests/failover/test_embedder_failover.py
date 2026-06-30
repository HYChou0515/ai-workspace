"""LitellmEmbedder transient-error retry + same-model replica failover (#196/#249).

Embeddings are non-streaming, so resilience is "retry the blip, then switch": the
chain is the primary base_url + the configured replica base_urls, all running the
SAME model (a different model would corrupt the vector space). A transient blip
(a 502/503, a dropped connection) is retried on the SAME endpoint a few times
before switching; a permanent error (a 400) is never retried. ``sleep`` is
injected so the backoff never actually waits.
"""

from __future__ import annotations

import litellm
import pytest

from workspace_app.kb.embedder import LitellmEmbedder


class _Busy(Exception):
    """A transient endpoint error, like litellm's 503 ServiceUnavailable."""

    status_code = 503


class _BadRequest(Exception):
    """A permanent error — retrying can never make a 400 succeed."""

    status_code = 400


def _patch(monkeypatch, behavior):
    """Patch litellm.embedding to record every endpoint hit (including failed
    attempts) and run ``behavior(api_base, attempt_index)``, which may raise."""
    used: list[str | None] = []

    def fake(*, model, input, timeout, num_retries, api_base, api_key):
        used.append(api_base)
        behavior(api_base, len(used) - 1)  # may raise to simulate a failure
        return type("R", (), {"data": [{"embedding": [1.0]} for _ in input]})()

    monkeypatch.setattr(litellm, "embedding", fake)
    return used


def _embedder(**kw) -> LitellmEmbedder:
    # num_retries=4 → m=5 quick shots per endpoint (the index-time default);
    # round_backoff_s=() → single sweep (re-sweep rounds are covered in test_retry).
    kw.setdefault("num_retries", 4)
    kw.setdefault("round_backoff_s", ())
    return LitellmEmbedder("bge-m3", dim=1, sleep=lambda _s: None, **kw)


def test_single_endpoint_success_calls_once(monkeypatch):
    used = _patch(monkeypatch, lambda base, i: None)
    emb = _embedder(base_url="http://primary")
    assert emb.embed_query("hello") == [1.0]
    assert used == ["http://primary"]


def test_single_endpoint_retries_a_transient_blip_then_succeeds(monkeypatch):
    def behavior(base, i):
        if i == 0:
            raise _Busy()  # first attempt blips, then the endpoint recovers

    used = _patch(monkeypatch, behavior)
    emb = _embedder(base_url="http://primary")
    assert emb.embed_query("q") == [1.0]
    assert used == ["http://primary", "http://primary"]  # retried the SAME endpoint


def test_switches_to_replica_after_exhausting_primary_attempts(monkeypatch):
    def behavior(base, i):
        if base == "http://primary":
            raise _Busy()  # primary stays busy → use up its attempts, then switch

    used = _patch(monkeypatch, behavior)
    emb = _embedder(base_url="http://primary", fallback_base_urls=["http://replica"])
    assert emb.embed_documents(["a"]) == [[1.0]]
    assert used == ["http://primary"] * 5 + ["http://replica"]  # m=5 quick shots, then replica


def test_permanent_error_is_not_retried(monkeypatch):
    def behavior(base, i):
        raise _BadRequest()  # a 400 can never succeed on retry

    used = _patch(monkeypatch, behavior)
    emb = _embedder(base_url="http://primary")
    with pytest.raises(_BadRequest):
        emb.embed_query("q")
    assert used == ["http://primary"]  # one shot, no retry on a bad request
