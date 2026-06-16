"""KB_EMBED_DIM resolver — auto-derive from KB_EMBED_MODEL when not set.

The `DocChunk.embedding` Vector column's width is bound at module
import time, so the value MUST be known before resources/kb.py finishes
loading. Setting both `KB_EMBED_MODEL=openai/text-embedding-3-small`
AND `KB_EMBED_DIM=1536` is tedious and easy to forget; setting just
the model and forgetting the dim used to silently break inserts
(model emits 1536-dim vectors, column is 1024-wide, specstar
rejects). The resolver picks the right dim from a known-models
table when only the model is set.
"""

from __future__ import annotations

import pytest

from workspace_app.resources.kb import _resolve_code_embed_dim, _resolve_embed_dim


def test_explicit_kb_embed_dim_env_wins(monkeypatch):
    """When the operator set both, the explicit number is used —
    covers the 'I'm using an unlisted model and know its dim' case."""
    monkeypatch.setenv("KB_EMBED_MODEL", "openai/text-embedding-3-small")
    monkeypatch.setenv("KB_EMBED_DIM", "999")
    assert _resolve_embed_dim() == 999


def test_resolves_openai_text_embedding_3_small_to_1536(monkeypatch):
    monkeypatch.delenv("KB_EMBED_DIM", raising=False)
    monkeypatch.setenv("KB_EMBED_MODEL", "openai/text-embedding-3-small")
    assert _resolve_embed_dim() == 1536


def test_resolves_openai_text_embedding_3_large_to_3072(monkeypatch):
    monkeypatch.delenv("KB_EMBED_DIM", raising=False)
    monkeypatch.setenv("KB_EMBED_MODEL", "openai/text-embedding-3-large")
    assert _resolve_embed_dim() == 3072


def test_resolves_ollama_bge_m3_to_1024(monkeypatch):
    monkeypatch.delenv("KB_EMBED_DIM", raising=False)
    monkeypatch.setenv("KB_EMBED_MODEL", "ollama/bge-m3")
    assert _resolve_embed_dim() == 1024


def test_empty_model_resolves_to_offline_default(monkeypatch):
    """Empty model string is the offline HashEmbedder fallback (see
    `factories.get_embedder`). Default width matches the prior
    behaviour (1024) so existing offline setups don't change."""
    monkeypatch.delenv("KB_EMBED_DIM", raising=False)
    monkeypatch.setenv("KB_EMBED_MODEL", "")
    assert _resolve_embed_dim() == 1024


def test_no_env_set_falls_back_to_bge_m3_default(monkeypatch):
    """The deploy default in Settings is ollama/bge-m3 → 1024.
    Removing both env vars matches that — the prior import-time
    `int(os.getenv('KB_EMBED_DIM', '1024'))` behaviour is preserved."""
    monkeypatch.delenv("KB_EMBED_MODEL", raising=False)
    monkeypatch.delenv("KB_EMBED_DIM", raising=False)
    assert _resolve_embed_dim() == 1024


def test_unknown_model_with_no_explicit_dim_raises(monkeypatch):
    """An unfamiliar model without an explicit dim is a setup bug,
    not a silent default — fail loud at import time so the operator
    knows to set KB_EMBED_DIM."""
    monkeypatch.delenv("KB_EMBED_DIM", raising=False)
    monkeypatch.setenv("KB_EMBED_MODEL", "openai/some-future-model")
    with pytest.raises(ValueError, match="KB_EMBED_DIM"):
        _resolve_embed_dim()


def test_code_embed_dim_resolves_similarly(monkeypatch):
    """The code embedder has its own pair of env vars; the resolver
    follows the same shape (model-known → derive, explicit wins,
    unknown raises)."""
    monkeypatch.delenv("KB_CODE_EMBED_DIM", raising=False)
    monkeypatch.setenv("KB_CODE_EMBED_MODEL", "ollama/nomic-embed-text")
    assert _resolve_code_embed_dim() == 768

    monkeypatch.setenv("KB_CODE_EMBED_DIM", "555")
    assert _resolve_code_embed_dim() == 555


def test_code_embed_dim_no_env_returns_768_default(monkeypatch):
    """The deploy default matches the prior import-time fallback."""
    monkeypatch.delenv("KB_CODE_EMBED_MODEL", raising=False)
    monkeypatch.delenv("KB_CODE_EMBED_DIM", raising=False)
    assert _resolve_code_embed_dim() == 768
