"""#633 — hand the turn what the question named, instead of hoping it asks.

A 14B model decides whether to look something up by whether IT recognises the
word: 「回焊爐是什麼?」 was answered from general knowledge with the dossier one
call away, across four prompt attempts (#630 P7). So the names a question
mentions are resolved here and their facts ride along with it — the same
position and timing as the glossary block (#106), which has done this for
context cards since before the graph existed.

Everything in the rendered block is a bound, because it competes for the same
context the user's own documents need:

* only identities with something to SAY get in. A name with no facts costs
  context and returns nothing, and the extractor mints generic short names
  (「機台」, 「良率」) by the hundred — this rule is what keeps them out;
* each identity contributes a few facts, the block stops at a ceiling, and
  **every cut is stated in the text**. A block that quietly dropped half its
  content reads exactly like a complete one;
* an ambiguous name says how many things share it rather than picking one —
  picking silently answers about the wrong thing and nobody finds out;
* it reads AS THE CALLER. Injection must not become the one channel that leaks
  a name or a figure the caller cannot open.
"""

from __future__ import annotations

from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError

from ...resources.graph import GraphEntity
from .name_index import NameIndex
from .normalize import display_value
from .review import EntityPage, entity_page

# How many named things one question may bring along. A question naming five
# things is usually naming one thing and four incidentals.
MAX_ENTITIES = 3
# Facts per thing — enough to answer "what is it", not enough to be a document.
MAX_FACTS = 8
# The whole block. Sized to stay well inside a small model's window beside the
# question, the history and whatever the agent then retrieves.
MAX_CHARS = 4_000


def entity_block(
    spec: SpecStar,
    index: NameIndex,
    text: str,
    *,
    as_user: str,
    max_entities: int = MAX_ENTITIES,
    max_facts: int = MAX_FACTS,
    max_chars: int = MAX_CHARS,
) -> str:
    """The context block for every indexed name ``text`` mentions, or ``""``.

    Empty is the common case and must stay cheap: a question naming nothing
    known does one set intersection and returns.
    """
    hits = index.hits(text)
    if not hits:
        return ""
    erm = spec.get_resource_manager(GraphEntity)
    cards: list[str] = []
    skipped = 0
    for name in sorted(hits, key=lambda n: (-len(n), n)):  # longest name first
        ids = hits[name]
        if len(cards) >= max_entities:
            skipped += 1
            continue
        card = _card(spec, erm, ids, as_user=as_user, max_facts=max_facts, sharing=len(ids))
        if card:
            cards.append(card)
    if not cards:
        return ""
    body = "\n\n".join(cards)
    if len(body) > max_chars:
        # Cut whole cards, never mid-fact: half a statement is worse than none.
        kept: list[str] = []
        used = 0
        for card in cards:
            if used + len(card) > max_chars:
                break
            kept.append(card)
            used += len(card) + 2
        skipped += len(cards) - len(kept)
        body = "\n\n".join(kept)
    head = "## What the knowledge base records about names in this question"
    tail = (
        "\nUse `lookup_entity` on any name above for its full file "
        "(every document that mentions it, all its connections)."
    )
    if skipped:
        tail = f"\n{skipped} more named thing(s) matched but were left out for space." + tail
    return f"{head}\n\n{body}\n{tail}"


def _card(
    spec: SpecStar,
    erm: object,
    ids: tuple[str, ...],
    *,
    as_user: str,
    max_facts: int,
    sharing: int,
) -> str:
    """One named thing, or ``""`` when it is unreadable or has nothing to say."""
    page: EntityPage | None = None
    for rid in ids:
        try:
            candidate = entity_page(spec, rid, as_user=as_user)
        except ResourceIDNotFoundError:
            continue  # unreadable — indistinguishable from unknown, by design
        if candidate.claims or candidate.value_of:
            page = candidate
            break
    if page is None:
        return ""  # nothing readable, or nothing stated: injecting a bare name
        # spends context and returns nothing.

    entity = page.entity
    kind = ""
    if entity.kind_id:
        try:
            kind = entity_page(spec, entity.kind_id, as_user=as_user).entity.canonical_name
        except ResourceIDNotFoundError:
            kind = ""
    title = entity.canonical_name + (f"({kind})" if kind else "")
    lines = [f"### {title}"]
    if sharing > 1:
        # Do not choose for the reader — say the name is ambiguous and let the
        # agent disambiguate or ask.
        lines.append(
            f"⚠ {sharing} different things in the knowledge base go by this name; "
            "the facts below are one of them. `lookup_entity` shows the others."
        )
    others = sorted({m.surface for m in page.mentions} - {entity.canonical_name})
    if others:
        lines.append("Also written: " + ", ".join(others[:5]))

    facts: list[str] = []
    for c in page.claims:
        period = f" ({c.period})" if c.period else ""
        facts.append(
            f"- {c.attribute} = {display_value(c.value, c.unit)}{period} [{c.source_doc_id}]"
        )
    for c in page.value_of:
        facts.append(f"- is the {c.attribute} of {c.subject} [{c.source_doc_id}]")
    for r in page.related[:max_facts]:
        arrow = (
            f"{entity.canonical_name} —{r.predicate}→ {r.other_name}"
            if r.direction == "out"
            else f"{r.other_name} —{r.predicate}→ {entity.canonical_name}"
        )
        facts.append(f"- {arrow} [{r.source_doc_id}]")

    shown, dropped = facts[:max_facts], max(0, len(facts) - max_facts)
    lines.extend(shown)
    if dropped:
        lines.append(f"- …and {dropped} more, via `lookup_entity`")
    return "\n".join(lines)
