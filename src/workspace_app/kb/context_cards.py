"""Context cards (#106) — a lightweight, deterministic glossary path beside
``kb_search``. A card maps several ``keys`` (term + surface forms) to a short
``body`` explanation; lookups are exact key membership over a derived,
normalised ``norm_keys`` — no embedding, no LLM, no agent loop.

This module owns the deterministic core: ``norm`` (the normalisation every
caller, internal or external, can replicate) and ``derive_norm_keys`` (the
indexed lookup surface materialised on write).
"""

from __future__ import annotations

import unicodedata


def norm(s: str) -> str:
    """Deterministic key normalisation, shared by author-time derivation and
    every lookup. NFKC (fold full/half-width + compatibility) → ``casefold``
    (Unicode-aware lower) → collapse all whitespace runs to single spaces and
    strip. Intentionally simple so an external caller can reproduce it exactly.
    """
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def derive_norm_keys(keys: list[str]) -> list[str]:
    """The indexed lookup surface materialised on write: each key normalised,
    blanks dropped, deduped, and sorted so the stored list is deterministic
    (stable across re-authoring with the same keys in a different order)."""
    return sorted({n for k in keys if (n := norm(k))})
