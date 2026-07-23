"""Attribute-claim extraction (#534 slice 1, re-cut by #630).

Ask a VLM-markdown chunk for every statement of the form 「誰 · 什麼屬性 · 值」
— `(subject, attribute, value)` plus the optional `unit` and `period` — as JSON,
and parse it.

**The value's type is not a gate.** The first cut asked only for metrics "that
carry a numeric value", which made "the reflow oven's Q3 yield is 98.7%" a
first-class fact and "N5 TV0's recipe is PPOOIXUX" unrepresentable — the second
one then got picked up by relationship extraction instead, turning a VALUE into
a node with its own identity and its own merge decisions. No knowledge-graph
builder in the field gates on type (the #628 survey: GraphRAG, cognee,
LlamaIndex and LangChain all take any value; LangChain even forces `value: str`),
and the type says nothing about whether the statement matters. So: any value.

**Every statement names its subject.** Without one, a figure could only be filed
by co-location — "it was on the same slide" — a guess that gets worse the more
the slide talks about. The passage almost always says whose number it is; asking
costs nothing and replaces the guess with what was written.

The value is kept VERBATIM ("1.2M", "15%", "PPOOIXUX"): parsing it into a
canonical number is a later, app-side concern, and for a recipe name there is
nothing to parse. Robust: a non-JSON / malformed reply yields `[]` (never
raises), and entries missing a subject, an attribute or a value are dropped.
Every call streams (`ILlm.collect`).

Two layers of qualifier, following the split every mature graph model makes
(Wikidata quantity-vs-qualifier, RDF typed literals vs reification):

* `unit` belongs to the VALUE — "98.7" and "%" are one quantity;
* `period` belongs to the STATEMENT — when the claim held, not what the value is.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..llm import ILlm

_LOGGER = logging.getLogger(__name__)

_PROMPT = (
    "List every statement in the passage below that gives some THING a VALUE — "
    "a property, a setting, a measurement, an identifier, a name it was assigned. "
    'Anything of the shape 「X 的 A 是 V」 / "X\'s A is V", including a table row '
    "pairing a label with a value.\n\n"
    "For each, give:\n"
    '- "subject": the thing the statement is ABOUT, exactly as the passage writes '
    "it. If the passage does not say which thing, skip the statement.\n"
    '- "attribute": what is being given, in the passage\'s own words (良率 / '
    "recipe / 供應商 / temperature). There is no fixed list.\n"
    '- "value": the value VERBATIM — keep the original formatting exactly '
    '("1.2M", "98.7", "PPOOIXUX", "Ar/O2"). Do not convert, round or translate '
    "it.\n"
    '- "unit": the unit that belongs to the value ("%", "USD", "°C"), or an '
    "empty string when it has none.\n"
    '- "period": when the statement holds ("FY24 Q3", "2025 上半年"), or an empty '
    "string when the passage does not scope it in time.\n\n"
    'Output ONLY a JSON array of objects with keys "subject", "attribute", '
    '"value", "unit", "period" — no prose.\n\nPassage:\n{text}'
)


@dataclass(frozen=True)
class AttributeClaim:
    """One statement a passage made about a thing.

    ``value`` is the verbatim surface form, whatever its type. ``unit`` qualifies
    the VALUE; ``period`` qualifies the STATEMENT. Both are ``""`` when absent.
    """

    subject: str
    attribute: str
    value: str
    unit: str = ""
    period: str = ""


def extract_claims(llm: ILlm, text: str) -> list[AttributeClaim]:
    """Extract the passage's attribute statements. Never raises."""
    reply = llm.collect(_PROMPT.format(text=text))
    start, end = reply.find("["), reply.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []  # no JSON array in the reply
    try:
        data = json.loads(reply[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        _LOGGER.warning("extract_claims: malformed JSON reply, dropping the batch")
        return []
    if not isinstance(data, list):
        return []
    out: list[AttributeClaim] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject", "")).strip()
        attribute = str(item.get("attribute", "")).strip()
        value = str(item.get("value", "")).strip()
        # All three are load-bearing: no subject ⇒ nothing to file it under, no
        # attribute ⇒ it says nothing, no value ⇒ it states nothing.
        if not subject or not attribute or not value:
            continue
        out.append(
            AttributeClaim(
                subject=subject,
                attribute=attribute,
                value=value,
                unit=str(item.get("unit", "")).strip(),
                period=str(item.get("period", "")).strip(),
            )
        )
    return out
