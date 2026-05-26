"""Stable, slash-free SourceDoc ids.

A doc's natural key is ``{collection_id}/{user}/{path}`` (``path`` itself may be
nested, e.g. ``manuals/reflow/guide.md``). The id gets embedded in
``kb://doc/{id}`` links (split on ASCII ``/``), so it must contain no ASCII
``/``. specstar itself accepts almost any character, so rather than
percent-encoding the whole key (which mangles ``:``, spaces, unicode — the id
is shown to people, so readability matters), we keep the key as-is and only swap
each ``/`` for the look-alike division slash ``∕`` (U+2215). The id then reads
just like the path but never contains a real ``/``.

The id is an OPAQUE handle: composed from the natural key here (so the same
logical doc always maps to the same id — dedup / idempotent re-upload) but never
decomposed. To recover ``collection`` / ``user`` / ``path``, read the SourceDoc's
fields (``path``, ``collection_id``) and ``created_by`` meta — never parse the id.

The only theoretical collision is a path that *literally* contains U+2215 ``∕``;
real (ASCII) file paths don't, so we accept it rather than reintroduce an ugly
two-char escape that would hurt readability.
"""

from __future__ import annotations

_SLASH = "∕"  # ∕ DIVISION SLASH — a '/'-look-alike that is not ASCII '/'


def encode_doc_id(collection_id: str, user: str, path: str) -> str:
    """Compose the opaque, readable, slash-free SourceDoc id from its natural
    key — every ASCII ``/`` (the separators and any in a nested path) becomes
    ``∕`` (U+2215)."""
    return f"{collection_id}/{user}/{path}".replace("/", _SLASH)
