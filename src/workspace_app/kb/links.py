"""Cross-reference link rewriting for KB documents.

When an archive of markdown files references siblings (``[Foo](./foo.md)``),
those relative links break once each file becomes its own SourceDoc. At render
time we rewrite a relative link to the sibling's stable logical URI
``kb://doc/{resource_id}`` (resolved to a real route late, on the FE) — but only
when that sibling actually exists in the KB; otherwise the link is left as-is.

Because a SourceDoc's id is the natural key ``{collection}/{user}/{path}``, the
target id is computed directly from the referencing doc's path — no separate
mapping needed. This is a pure function (existence is injected) so it's easy to
test.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable

# Markdown inline links / images: [text](target) and ![alt](target "title").
_MD_LINK = re.compile(r'(!?\[[^\]]*\]\()([^)\s]+)((?:\s+"[^"]*")?\))')
_EXTERNAL = ("http://", "https://", "//", "mailto:", "#")


def rewrite_md_links(
    markdown: str,
    *,
    doc_path: str,
    collection_id: str,
    user: str,
    exists: Callable[[str], bool],
) -> str:
    base = posixpath.dirname(doc_path)

    def repl(m: re.Match[str]) -> str:
        pre, target, post = m.group(1), m.group(2), m.group(3)
        return f"{pre}{_resolve(target, base, collection_id, user, exists)}{post}"

    return _MD_LINK.sub(repl, markdown)


def _resolve(
    target: str, base: str, collection_id: str, user: str, exists: Callable[[str], bool]
) -> str:
    if target.startswith(_EXTERNAL):
        return target
    path, sep, frag = target.partition("#")
    frag = f"{sep}{frag}" if sep else ""
    resolved = posixpath.normpath(posixpath.join(base, path))
    rid = f"{collection_id}/{user}/{resolved}"
    return f"kb://doc/{rid}{frag}" if exists(rid) else target
