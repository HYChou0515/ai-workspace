"""Cross-reference link rewriting for KB documents.

When an archive of markdown files references siblings (``[Foo](./foo.md)``,
``![pic](./diagram.png)``), those relative links break once each file becomes
its own SourceDoc. At render time we resolve each relative link to a
STABLE URL produced by the caller-supplied ``resolve`` function:

- Text/markdown sibling → ``kb://doc/{rid}`` (the FE turns this into in-app
  navigation).
- Image / blob sibling → specstar's ``/blobs/{file_id}`` URL (the FE renders
  the resulting ``<img src=...>`` directly; specstar serves the bytes with the
  Content-Type stored at upload time).

A SourceDoc's id is its natural key ``{collection}/{path}`` encoded slash-free
(see ``kb.doc_id``), so the sibling's id is composed directly from the
referencing doc's collection + resolved-path — no mapping table, and no user
(a path is one shared doc in the collection). The link decision (which kind of
URL to emit for a given rid) lives in the caller's ``resolve`` so this module
stays pure / easy to unit-test.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable

from .doc_id import encode_doc_id

# Markdown inline links / images: [text](target) and ![alt](target "title").
_MD_LINK = re.compile(r'(!?\[[^\]]*\]\()([^)\s]+)((?:\s+"[^"]*")?\))')
_EXTERNAL = ("http://", "https://", "//", "mailto:", "#")


def rewrite_md_links(
    markdown: str,
    *,
    doc_path: str,
    collection_id: str,
    resolve: Callable[[str], str | None],
) -> str:
    """Rewrite relative links in ``markdown`` to the URLs ``resolve`` produces.

    ``resolve(rid)`` returns either a URL string for that SourceDoc id or
    ``None`` when no SourceDoc matches (the link is left untouched in that
    case). The caller decides per-rid which scheme to emit — typically
    ``kb://doc/{rid}`` for text and ``/blobs/{file_id}`` for binaries.
    """
    base = posixpath.dirname(doc_path)

    def repl(m: re.Match[str]) -> str:
        pre, target, post = m.group(1), m.group(2), m.group(3)
        return f"{pre}{_rewrite(target, base, collection_id, resolve)}{post}"

    return _MD_LINK.sub(repl, markdown)


# Issue #41: when the link target omits an extension (e.g.
# `[B](./b)`), try each of these suffixes in order after the literal
# match fails. Authors writing markdown-with-cross-refs commonly drop
# `.md` (paths read cleaner); without this, `[B](./b)` would dangle
# even though `b.md` was uploaded in the same batch.
_EXTENSION_FALLBACKS = (".md", ".mdx", ".markdown")


def _rewrite(
    target: str,
    base: str,
    collection_id: str,
    resolve: Callable[[str], str | None],
) -> str:
    if target.startswith(_EXTERNAL):
        return target
    path, sep, frag = target.partition("#")
    frag = f"{sep}{frag}" if sep else ""
    resolved = posixpath.normpath(posixpath.join(base, path))
    # Literal match first — if the link target already names the
    # sibling's full path, the original `[Foo](./foo.md)` behaviour
    # holds.
    rid = encode_doc_id(collection_id, resolved)
    url = resolve(rid)
    if url is not None:
        return _attach_fragment(url, frag)
    # Issue #41: try common markdown extensions only when the target
    # has NO extension of its own (`posixpath.splitext` returns `''`
    # for the suffix). Targets like `./diagram.png` are left alone —
    # picking up `diagram.png.md` would be surprising.
    if not posixpath.splitext(resolved)[1]:
        for ext in _EXTENSION_FALLBACKS:
            cand_rid = encode_doc_id(collection_id, resolved + ext)
            cand_url = resolve(cand_rid)
            if cand_url is not None:
                return _attach_fragment(cand_url, frag)
    return target


def _attach_fragment(url: str, frag: str) -> str:
    """Append a markdown fragment (``#section``) to ``url``. Skipped for
    non-text URLs (e.g. ``/blobs/{id}``) since fragments are
    text-document affordances; the caller's ``resolve`` decides which
    URL form to emit, so we only attach when the form would render
    fragments (``kb://doc/...``)."""
    if not frag:
        return url
    if url.startswith("kb://doc/"):
        return f"{url}{frag}"
    return url
