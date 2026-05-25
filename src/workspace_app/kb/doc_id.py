"""Stable, slash-free SourceDoc ids.

A doc's natural key is ``{collection_id}/{user}/{path}`` (``path`` itself may be
nested, e.g. ``manuals/reflow/guide.md``). specstar resource ids can't contain
``/``, so we percent-encode the whole key into a single slash-free token.

The id is an OPAQUE handle: it's composed from the natural key here (so the same
logical doc always maps to the same id — dedup / idempotent re-upload), but it is
never decomposed. To recover ``collection`` / ``user`` / ``path``, read the
SourceDoc's fields (``path``, ``collection_id``) and ``created_by`` meta — never
parse the id.
"""

from __future__ import annotations

from urllib.parse import quote


def encode_doc_id(collection_id: str, user: str, path: str) -> str:
    """Compose the opaque, slash-free SourceDoc id from its natural key.
    ``safe=""`` escapes every ``/`` (the separators and any in a nested path)."""
    return quote(f"{collection_id}/{user}/{path}", safe="")
