"""Stable, slash-free SourceDoc ids.

A doc's natural key is ``{collection_id}/{path}`` (``path`` itself may be nested,
e.g. ``manuals/reflow/guide.md``). A collection is a SHARED space — a given path
is ONE document regardless of who uploaded it, so two users writing the same
path update the same doc (last write wins; ``created_by`` stays the original
uploader, ``updated_by`` tracks the latest). The id gets embedded in
``kb://doc/{id}`` links (split on ASCII ``/``), so it must contain no ASCII
``/``. specstar itself accepts almost any character, so rather than
percent-encoding the whole key (which mangles ``:``, spaces, unicode — the id
is shown to people, so readability matters), we keep the key as-is and only swap
each ``/`` for the look-alike division slash ``∕`` (U+2215). The id then reads
just like the path but never contains a real ``/``.

The id is an OPAQUE handle: composed from the natural key here (so the same
logical doc always maps to the same id — dedup / idempotent re-upload) but never
decomposed. To recover ``collection`` / ``path`` / ``created_by``, read the
SourceDoc's fields (``path``, ``collection_id``) and its meta — never parse it.

The only theoretical collision is a path that *literally* contains U+2215 ``∕``;
real (ASCII) file paths don't, so we accept it rather than reintroduce an ugly
two-char escape that would hurt readability.
"""

from __future__ import annotations

_SLASH = "∕"  # ∕ DIVISION SLASH — a '/'-look-alike that is not ASCII '/'


def canonical_path(path: str) -> str:
    """Canonical, relative form of a document path: leading slashes stripped.

    A doc's id is keyed on its path, so surface variants of the SAME logical
    path (``/a.md`` vs ``a.md``) MUST collapse here before ``encode_doc_id`` —
    otherwise one file becomes two docs. Repeated slashes, ``.`` segments and an
    inner ``..`` resolve away too. Every ingest / move entry point routes its
    path through this so "a path is ONE document" holds regardless of caller.
    """
    stack: list[str] = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not stack:
                raise ValueError(f"path escapes its root: {path!r}")
            stack.pop()
        else:
            stack.append(seg)
    return "/".join(stack)


def encode_doc_id(collection_id: str, path: str) -> str:
    """Compose the opaque, readable, slash-free SourceDoc id from its natural
    key ``{collection_id}/{path}`` — every ASCII ``/`` (the separator and any in
    a nested path) becomes ``∕`` (U+2215). Path-keyed, NOT per-user: a path is
    one shared document in the collection."""
    return f"{collection_id}/{path}".replace("/", _SLASH)
