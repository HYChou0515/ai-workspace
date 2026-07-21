"""#534 B — extract what a passage MENTIONS, verbatim (the primary layer).

The knowledge graph is built in two layers. This is the lower one: whatever the
text talks about, recorded exactly as the text wrote it. It never merges, never
normalises and never filters by kind. Deciding that two mentions are the same
thing is the upper layer's job, made once against ALL the accumulated evidence
rather than guessed one passage at a time — and made as a LINK, so the record
here is never rewritten and a wrong decision costs nothing to undo.

Two things follow, and both are deliberate.

**The surface is verbatim.** It is the evidence. Every comparison key downstream
is derived from it by rules that will be revised, and a normalisation baked in
here could only be revised by re-running the model over the whole corpus.

**The kind is free text, not a fixed list.** The kinds that matter are whatever
the corpus is about, and a list written in advance can only be wrong in the
expensive direction: anything outside it is silently forced into a neighbour or a
catch-all. So the model says what kind of thing it saw, in the document's own
words, and the labels ("機台" / "tool" / "設備") are unified afterwards by the
same mechanism that unifies everything else — the taxonomy comes out of the data
instead of being imposed on it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..llm import ILlm

_LOGGER = logging.getLogger(__name__)

_PROMPT = (
    "List everything the passage below talks about — equipment, processes, "
    "materials, defects, parameters, measurements, part numbers, products, "
    "organisations, anything a reader would consider a distinct thing.\n\n"
    "For each, give:\n"
    '- "surface": the term EXACTLY as the passage writes it, in the same script '
    "and spelling. Do not translate, expand, abbreviate or tidy it.\n"
    '- "kind": what sort of thing it is, in the passage\'s own words (a short '
    "noun such as 機台 / 製程 / 材料 / defect / parameter). Use the vocabulary "
    "the passage itself uses; there is no fixed list to choose from.\n\n"
    'Output ONLY a JSON array of objects with keys "surface" and "kind" — no '
    "prose.\n\nPassage:\n{text}"
)


@dataclass(frozen=True)
class EntityMention:
    """One thing a passage mentions. ``surface`` is the verbatim term; ``kind`` is
    the passage's own word for what sort of thing it is, ``""`` when unstated."""

    surface: str
    kind: str = ""


def extract_entities(llm: ILlm, text: str) -> list[EntityMention]:
    """Extract the passage's mentions. Never raises.

    A reply that is not a JSON array — refusal, commentary, a truncated
    generation — yields no mentions rather than failing, so one bad passage
    cannot take down the batch it rides in. Entries with no surface are dropped:
    a kind with nothing to attach it to is not a mention of anything.

    Occurrences are NOT collapsed here. The same term appearing twice is two
    entries, because how often a document mentions something is a signal, and the
    writer aggregates it across the whole document rather than per passage.
    """
    reply = llm.collect(_PROMPT.format(text=text))
    start, end = reply.find("["), reply.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []  # no JSON array in the reply
    try:
        data = json.loads(reply[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        _LOGGER.warning("extract_entities: malformed JSON reply, dropping the batch")
        return []
    if not isinstance(data, list):
        return []
    out: list[EntityMention] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        surface = str(item.get("surface", "")).strip()
        if not surface:
            continue
        out.append(EntityMention(surface=surface, kind=str(item.get("kind", "")).strip()))
    return out
