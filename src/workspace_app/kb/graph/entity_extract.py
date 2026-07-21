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
from typing import Any

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
    "Then, ONLY if the passage explicitly says two names are the same thing "
    '("以下簡稱", "又稱", "aka", "i.e."), list those pairs. For each give '
    '"a", "b", and "quote" — the words from the passage that say so, copied '
    "exactly. If the passage does not say it, do not list it: a resemblance you "
    "noticed is not a declaration.\n\n"
    "Output ONLY a JSON object:\n"
    '{{"mentions": [{{"surface": ..., "kind": ...}}], '
    '"aliases": [{{"a": ..., "b": ..., "quote": ...}}]}}\n'
    "No prose.\n\nPassage:\n{text}"
)


@dataclass(frozen=True)
class DeclaredAlias:
    """An equivalence the passage STATES, with the words that state it.

    The quote is the whole point. A model reporting "the text says these are the
    same" and a model judging "these look similar" both come out of the same
    model, but the first points at a sentence anyone can read and the second
    points at nothing — and that is the line between applying a link and queueing
    it for a person. The quote is what keeps the distinction a checkable
    condition rather than a label the model awards itself.
    """

    a: str
    b: str
    quote: str


@dataclass(frozen=True)
class Extraction:
    """What one passage yielded: the things it talks about, and the equivalences
    it states about them."""

    mentions: list[EntityMention]
    aliases: list[DeclaredAlias]


@dataclass(frozen=True)
class EntityMention:
    """One thing a passage mentions. ``surface`` is the verbatim term; ``kind`` is
    the passage's own word for what sort of thing it is, ``""`` when unstated."""

    surface: str
    kind: str = ""


def extract_entities(llm: ILlm, text: str) -> Extraction:
    """Extract the passage's mentions and the equivalences it declares. Never
    raises.

    A reply that is neither the expected object nor a bare mention array — a
    refusal, commentary, a truncated generation — yields nothing rather than
    failing, so one bad passage cannot take down the batch it rides in. The bare
    array is still accepted because small models drift back to the simpler shape
    they were asked for last time.

    Entries with no surface are dropped: a kind with nothing to attach it to is
    not a mention of anything. Occurrences are NOT collapsed here — how often a
    document mentions something is a signal, and the writer aggregates it across
    the whole document rather than per passage.

    A declared alias is kept only if it is complete AND its quote really appears
    in the passage. Without that check the requirement would be decoration: a
    model free to invent the sentence has given nobody anything to verify.
    """
    reply = llm.collect(_PROMPT.format(text=text))
    payload = _parse(reply)
    if payload is None:
        return Extraction(mentions=[], aliases=[])
    raw_mentions = payload.get("mentions")
    raw_aliases = payload.get("aliases")
    mentions: list[EntityMention] = []
    for item in raw_mentions if isinstance(raw_mentions, list) else []:
        if not isinstance(item, dict):
            continue
        surface = str(item.get("surface", "")).strip()
        if not surface:
            continue
        mentions.append(EntityMention(surface=surface, kind=str(item.get("kind", "")).strip()))
    aliases: list[DeclaredAlias] = []
    for item in raw_aliases if isinstance(raw_aliases, list) else []:
        if not isinstance(item, dict):
            continue
        a = str(item.get("a", "")).strip()
        b = str(item.get("b", "")).strip()
        quote = str(item.get("quote", "")).strip()
        if not (a and b and quote):
            continue
        if quote not in text:
            _LOGGER.warning("extract_entities: dropped an alias whose quote is not in the passage")
            continue
        aliases.append(DeclaredAlias(a=a, b=b, quote=quote))
    return Extraction(mentions=mentions, aliases=aliases)


def _parse(reply: str) -> dict[str, Any] | None:
    """The reply as a mapping, accepting either the object asked for or a bare
    mention array.

    Both shapes are attempted independently: a bare array's own braces make the
    object slice unparseable, so a failure on one shape must fall through to the
    other rather than condemn the reply.
    """
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = reply.find(opener), reply.rfind(closer)
        if start == -1 or end == -1 or end < start:
            continue
        try:
            data = json.loads(reply[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and ("mentions" in data or "aliases" in data):
            return data
        if isinstance(data, dict):
            continue  # a lone object from INSIDE a bare array — try the array shape
        if isinstance(data, list):
            return {"mentions": data, "aliases": []}
    _LOGGER.warning("extract_entities: no usable JSON in the reply, dropping the batch")
    return None
