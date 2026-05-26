"""Citation parsing — resolve the ``[n]`` markers an LLM answer cites into
Citation value objects, against the per-turn passage registry the kb_search
tool accumulated. Pure: the registry is passed in, nothing is loaded here.
"""

from __future__ import annotations

import re

from ..resources.kb import Citation, RetrievedPassage

# Markers may use ASCII "[1]", full-width "［1］" (U+FF3B/FF3D), or CJK "【1】"
# (U+3010/3011) — a model writing Chinese (Qwen) emits the wide forms. `\d`
# already matches full-width digits, and int() parses them.
_MARKER = re.compile(r"[\[［【]\s*(\d+)\s*[\]］】]")


def parse_citations(answer: str, passages: list[RetrievedPassage]) -> list[Citation]:
    """Each distinct in-range ``[n]`` in `answer` → a Citation built from
    `passages[n-1]`. Deduped, ascending; out-of-range markers are dropped."""
    markers = sorted({int(m.group(1)) for m in _MARKER.finditer(answer)})
    out: list[Citation] = []
    for n in markers:
        if 1 <= n <= len(passages):
            p = passages[n - 1]
            out.append(
                Citation(
                    marker=n,
                    collection_id=p.collection_id,
                    document_id=p.document_id,
                    filename=p.filename,
                    start=p.start,
                    end=p.end,
                    source_chunk_ids=p.source_chunk_ids,
                    snippet=p.text,
                )
            )
    return out
