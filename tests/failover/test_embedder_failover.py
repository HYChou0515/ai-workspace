"""LitellmEmbedder same-model replica failover (#196).

Embeddings are non-streaming, so failover is "try each endpoint, switch on
error". The chain is the primary base_url + the configured replica base_urls,
all running the SAME model (a different model would corrupt the vector space —
there is no field to even express one, so it's a structural guarantee).
"""

from __future__ import annotations

import litellm

from workspace_app.failover.cooldown import CooldownRegistry
from workspace_app.kb.embedder import LitellmEmbedder


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _fake_embedding(monkeypatch, *, busy: set[str | None]):
    """Patch litellm.embedding to fail for endpoints in ``busy`` and otherwise
    return a deterministic 1-d vector per input, recording the endpoint used."""
    used: list[str | None] = []

    def fake(*, model, input, timeout, num_retries, api_base, api_key):
        used.append(api_base)
        if api_base in busy:
            raise RuntimeError(f"{api_base} is busy")
        return type("R", (), {"data": [{"embedding": [1.0]} for _ in input]})()

    monkeypatch.setattr(litellm, "embedding", fake)
    return used


def _embedder(reg, **kw) -> LitellmEmbedder:
    return LitellmEmbedder("bge-m3", dim=1, cooldown_registry=reg, cooldown_s=30.0, **kw)


def test_single_endpoint_when_no_replicas(monkeypatch):
    used = _fake_embedding(monkeypatch, busy=set())
    reg = CooldownRegistry(clock=_Clock())
    emb = _embedder(reg, base_url="http://primary")
    assert emb.embed_query("hello") == [1.0]
    assert used == ["http://primary"]


def test_switches_to_replica_when_primary_is_busy(monkeypatch):
    used = _fake_embedding(monkeypatch, busy={"http://primary"})
    clock = _Clock()
    reg = CooldownRegistry(clock=clock)
    emb = _embedder(reg, base_url="http://primary", fallback_base_urls=["http://replica"])
    assert emb.embed_documents(["a", "b"]) == [[1.0], [1.0]]
    assert used == ["http://primary", "http://replica"]  # tried primary, fell over
    assert reg.is_cooling(("bge-m3", "http://primary")) is True


def test_cooling_primary_is_skipped_on_next_call(monkeypatch):
    used = _fake_embedding(monkeypatch, busy={"http://primary"})
    reg = CooldownRegistry(clock=_Clock())
    reg.mark(("bge-m3", "http://primary"), 30.0)  # already known-busy
    emb = _embedder(reg, base_url="http://primary", fallback_base_urls=["http://replica"])
    assert emb.embed_query("q") == [1.0]
    assert used == ["http://replica"]  # primary skipped entirely
