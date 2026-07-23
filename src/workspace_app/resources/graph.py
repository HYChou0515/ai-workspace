"""Knowledge-graph resources (#534).

Two layers, and the split is what makes the whole thing safe to automate:

* **``GraphMention`` — the primary layer.** What a document said, verbatim, one
  row per (document, surface). Written by extraction and never rewritten by a
  later judgement.
* **``GraphEntity`` — the vocabulary (a later slice).** Shared identity across
  corpora. It does not own anything; it LINKS to mentions. So deciding that two
  surfaces are the same thing adds a link rather than destroying a record, which
  makes a wrong decision visible (an entry holding evidence that does not belong)
  and free to undo.

``GraphClaim`` (slice 1) is the same kind of evidence as a mention, specialised
to a measurement.
"""

from __future__ import annotations

import hashlib
from typing import Annotated

from msgspec import Struct
from specstar import OnDelete, Ref


class GraphClaim(Struct):  # → resource "graph-claim"
    """One statement a document made about a thing — 「誰 · 什麼屬性 · 值」 (#534
    slice 1, re-cut by #630).

    The value's TYPE is not a gate: "98.7" and "PPOOIXUX" are the same kind of
    fact, and the earlier numeric-only cut made half of them unrepresentable (see
    #630 and the #628 survey — no builder in the field gates on type). Two
    qualifier layers, following every mature graph model: ``unit`` belongs to the
    VALUE ("98.7" + "%" is one quantity); ``period`` belongs to the STATEMENT
    (when it held), not to what the value is.

    Rows written before #630 carry no subject and cannot be given one after the
    fact — the extraction never asked for it. They are re-created by the next
    per-doc extraction, which wipes then rewrites.

    Collection-scoped (permission inherits like SourceDoc / DocChunk via the
    cascade ``Ref``); ``source_doc_id`` / ``chunk_id`` are the provenance back to
    the source deck/slide. ``value`` is stored VERBATIM ("1.2M", "15%") —
    normalisation (parse → number + unit) is a later, app-side concern.

    Indexed: ``collection_id`` (Ref, auto), ``norm_subject`` / ``norm_attribute``
    / ``norm_value`` / ``norm_period`` (find a thing's statements, group an
    attribute across decks, and look up who holds a value), ``source_doc_id`` (so
    a re-extraction can wipe + rewrite one doc's claims). The ``norm_*`` keys are
    server-computed; the raw surfaces beside them are what a person reads.

    #534 slice 2: the ``collection_*`` / ``doc_*`` fields are a denormalized
    mirror of the READ permission of the deck this claim came from, so
    ``graph_claim_access_scope`` can hide the row at the storage layer, which is
    where the auto-CRUD read routes are covered without a hand-written guard on
    each. (``GET /{model}/export`` is the exception — it bypasses access scopes for
    every model in the app, ``source-doc`` and ``collection`` included, so it is a
    pre-existing hole this does not close.)
    They are written by the extractor and re-pushed by the permission fan-out;
    nothing else may set them. The mirror is the doc's EFFECTIVE permission, both
    layers: its collection's (#303) and its own tightening (#308).
    """

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    source_doc_id: str  # provenance: the deck/doc (indexed; NOT a cascade Ref)
    # #630: WHOSE attribute this is. Normalised with `norm_surface` — the same
    # rule entity surfaces use — because that is what lets a claim meet an
    # identity at all; a key computed by a different rule could never match one.
    norm_subject: str  # (indexed) — find every statement about one thing
    subject: str  # raw surface form (display), exactly as the passage wrote it
    norm_attribute: str  # normalised attribute key (indexed) — filter / group
    attribute: str  # raw surface form (display)
    value: str  # verbatim value, whatever its type ("1.2M", "15%", "PPOOIXUX")
    # #630: the value's key, ALSO `norm_surface` — a value that some document
    # elsewhere talks about as a subject is that identity, and matching the two
    # requires one shared rule. Indexed so "who has this as an attribute value"
    # is a query (「PPOO 系列被哪些機台使用」).
    norm_value: str = ""
    period: str = ""  # raw surface form (display)
    unit: str = ""  # raw surface form (display)
    # #534 甲: the comparison keys, DERIVED from the raw surfaces above by the pure
    # rules in `kb/graph/normalize.py` and stored because they must be INDEXED —
    # `exp_aggregate_by` groups on `indexed_data`, so a key that only existed at
    # read time could not be grouped on at all, and a filter on it would silently
    # match nothing. The raw surface stays beside each key as the thing a person
    # reads and the thing a re-derivation starts from.
    #
    # A derived-and-stored value is STATE, so it is versioned like state: changing
    # a rule bumps the `Schema` version and the migration step carries the new
    # algorithm (see `resources/__init__.py`). Every row then records WHICH version
    # of the rules produced its keys, which is what keeps an improved rule from
    # leaving older rows quietly on the old one.
    norm_period: str = ""
    norm_unit: str = ""
    chunk_id: str = ""  # provenance: the chunk / slide
    confidence: float = 1.0
    # --- #534 slice 2: the read-permission mirror (see the class docstring) ---
    # BOTH grant lists ride along at BOTH levels, because reading a claim needs
    # both answers: `read_meta` ("may you know this deck exists") and
    # `read_content` ("may you read it"). The lists are independent, and
    # "discoverable but not readable" is a state the product models on purpose —
    # so a claim, which IS content, needs the content grant, and a claim must not
    # become the one way to learn about a deck you cannot see.
    collection_visibility: str = ""  # the parent collection's visibility (#303)
    collection_read_meta: list[str] = []
    collection_read_content: list[str] = []
    collection_created_by: str = ""  # its owner — the authority every half matches
    # The deck's OWN verdict (#308), stated explicitly: a deck that adds no
    # restriction mirrors "public". "" is NOT "no override" — it is "no mirror was
    # ever written", which the scope reads as invisible. The default has to fail
    # CLOSED: a writer that forgets the mirror then loses rows (loud, someone
    # chases it) instead of publishing them (silent, nobody reports a leak).
    doc_visibility: str = ""
    doc_read_meta: list[str] = []
    doc_read_content: list[str] = []


def mention_id(source_doc_id: str, surface: str) -> str:
    """The id of a mention — content-addressed on (document, normalised surface).

    Derived rather than random so re-extraction is IDEMPOTENT: the same document
    saying the same thing again lands on the same row, so the vocabulary's links
    survive a prompt change, a model change or a re-run. A random id would break
    every link on every re-extraction and reset the vocabulary to nothing, which
    is the invariant #534 calls "identity stable across re-runs".

    Hashed rather than composed from the parts because a surface can hold any
    character (including the "/" a specstar id may not) and can be arbitrarily
    long. The raw surface stays on the row for display; the id is opaque and is
    never parsed.
    """
    from ..kb.graph.normalize import norm_surface

    digest = hashlib.blake2b(
        f"{source_doc_id}\x00{norm_surface(surface)}".encode(), digest_size=16
    ).hexdigest()
    return f"m{digest}"


class GraphMention(Struct):  # → resource "graph-mention"
    """One thing a document mentions — the primary layer (#534 B).

    Evidence, not judgement: ``surface`` is exactly what the document wrote and
    ``kind`` is the document's own word for what sort of thing it is. Neither is
    normalised in place, neither is merged with anything, and nothing here is
    rewritten when the vocabulary later decides two mentions name one thing —
    that decision is a link, held on the other side.

    ``kind`` is free text on purpose. The kinds that matter belong to the corpus,
    and a fixed list could only be wrong expensively: anything outside it gets
    forced into a neighbour. The labels ("機台" / "tool" / "設備") are unified by
    the SAME mechanism that unifies everything else, so the taxonomy comes out of
    the data instead of being imposed on it.

    The ``norm_*`` keys are derived state, stored because grouping reads
    ``indexed_data``, and therefore versioned: a rule change bumps the ``Schema``
    and the migration step carries the new algorithm. The permission mirror is
    the same seven fields a claim carries, read by the same scope — a mention is
    content, so it is exactly as visible as the document it came from.
    """

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    source_doc_id: str  # provenance: the deck/doc (indexed; NOT a cascade Ref)
    surface: str  # verbatim, as the document wrote it
    norm_surface: str = ""  # derived comparison key (indexed)
    kind: str = ""  # the document's own word for the sort of thing
    norm_kind: str = ""  # derived comparison key (indexed)
    occurrences: int = 1  # how often the document mentions it — an importance signal
    # An equivalence THIS DOCUMENT stated about this name ("回焊爐,以下簡稱 RO"),
    # with the words that state it. Primary-layer data: it is something the
    # document said, not a judgement about it. The quote is what lets the
    # vocabulary apply the link without asking a person — it points at a sentence
    # anyone can go and read, unlike a resemblance, which points at nothing.
    declared_same_as: list[str] = []  # normalised surfaces (indexed)
    declared_quote: str = ""
    chunk_ids: list[str] = []  # provenance: the chunks/slides it appeared on
    # --- the read-permission mirror, identical to GraphClaim's (see there) ---
    collection_visibility: str = ""
    collection_read_meta: list[str] = []
    collection_read_content: list[str] = []
    collection_created_by: str = ""
    doc_visibility: str = ""
    doc_read_meta: list[str] = []
    doc_read_content: list[str] = []


# The bases a link can rest on, ordered from "a person could go and check this"
# to "the model thought they looked alike". The order IS the policy: everything
# before `resembles` points at something verifiable — a deterministic rule, a
# sentence in a document, an earlier human decision — so it applies on its own.
# `resembles` points at nothing outside the model, so it waits for review. That
# line is the same one this whole design keeps drawing: an assertion that can name
# its evidence is worth more than one that merely sounds right.
LINK_BASES = ("identical", "declared", "approved", "resembles")


class GraphEntity(Struct):  # → resource "graph-entity"
    """A shared identity — the vocabulary layer (#534 B).

    It owns NOTHING. Saying "these mentions are the same thing" is a
    ``GraphEntityLink``, so a wrong grouping costs a link rather than a record:
    the mentions stay exactly as their documents wrote them, the mistake is
    visible (an entry holding evidence that does not belong) and undoing it loses
    nothing. That is what makes automating the decision acceptable at all.

    Identity is shared ACROSS collections, so it cannot inherit one collection's
    permission. ``collection_ids`` is the denormalized list of collections this
    identity has evidence in, and the access scope asks whether the caller can
    read any of them — an access scope is a predicate over ONE row and cannot ask
    another table what the caller may see, so the answer has to travel on the row.
    Empty ⇒ nothing vouches for this identity ⇒ nobody sees it: a name alone can
    leak (a customer code, an unreleased part), so it must not appear on the
    strength of merely existing.

    ``kind_id`` points at ANOTHER ``GraphEntity`` — a kind ("機台", with aliases
    "tool" / "設備") is an identity like any other, unified by the same mechanism,
    so the merge code is written once and the taxonomy comes out of the data. The
    recursion stops at a kind, whose own ``kind_id`` is empty.
    """

    canonical_name: str  # the display form — one of the surfaces a document used
    norm_keys: list[str] = []  # every surface that resolves here (derived, indexed)
    kind_id: str = ""  # → another GraphEntity; "" on a kind itself
    collection_ids: list[str] = []  # where its evidence lives — drives visibility
    # Set when this identity was absorbed into another: its keys and evidence
    # moved there, and this row stays as a tombstone. Kept rather than deleted
    # because a merge has to be undoable — a row that cannot say where it went is
    # a dead end, and an empty identity with no explanation looks like corruption
    # to whoever finds it next. Empty collections already make it invisible.
    merged_into: str = ""


class GraphEntityLink(Struct):  # → resource "graph-entity-link"
    """One claim that a mention belongs to an identity, WITH its basis.

    Separate from both sides on purpose. On the mention it would rewrite the
    primary layer, which must stay untouched; on the entity it would be an id list
    with nowhere to record WHY — and a vocabulary whose links cannot be told apart
    ("the document said so" vs "the model thought so") is one nobody can audit,
    which brings back the silence the two-layer split existed to remove.
    """

    entity_id: Annotated[str, Ref("graph-entity", on_delete=OnDelete.cascade)]
    mention_id: str  # → GraphMention (indexed; NOT a cascade Ref)
    basis: str = "resembles"  # one of LINK_BASES
    evidence: str = ""  # where to go and check: a quoted sentence, a rule, a user id
    # The collection the linked mention's evidence lives in, copied at creation.
    # A link is not neutral bookkeeping: its `evidence` can hold a sentence quoted
    # out of a document, so it needs the same "can you read where this came from"
    # gate its mention does. One element, so the identity scope serves both.
    collection_ids: list[str] = []
    state: str = "active"  # active | pending (awaiting review) | rejected
    # On a PENDING proposal: the identity this link would absorb. Accepting the
    # proposal is the same absorption a declaration performs; rejecting it leaves
    # nothing behind, because nothing was changed to make it.
    proposed_from: str = ""


def relationship_id(
    source_doc_id: str, chunk_id: str, subject: str, predicate: str, obj: str
) -> str:
    """Content-addressed on what said it and what it said, so a re-extraction lands
    on the same row and nothing accumulates."""
    from ..kb.graph.normalize import norm_surface

    parts = "\x00".join(
        [source_doc_id, chunk_id, norm_surface(subject), norm_surface(predicate), norm_surface(obj)]
    )
    return f"r{hashlib.blake2b(parts.encode(), digest_size=16).hexdigest()}"


class GraphRelationship(Struct):  # → resource "graph-relationship"
    """What one document said connects two things — the leg that makes this a
    graph rather than a list (#534 B).

    Evidence, like a mention: never rewritten, keyed on what said it, and carrying
    its document's read permission. It repeats a sentence's content — including,
    when the document was explicit, the sentence itself — so it is exactly as
    visible as that sentence.

    The predicate is free text and unified by the same mechanism as a kind: "造成"
    and "leads to" are one connection written two ways, and which connections
    matter belongs to the corpus rather than to a list written in advance by
    someone outside it.

    The ends are SURFACES, not entity ids. Resolving them to identities is the
    vocabulary's job and changes as the vocabulary learns; freezing an id here
    would make this row a second place identity lives, and the two would drift.
    """

    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    source_doc_id: str
    subject: str  # verbatim
    predicate: str  # verbatim
    object: str  # verbatim
    norm_subject: str = ""  # derived comparison keys (indexed)
    norm_predicate: str = ""
    norm_object: str = ""
    chunk_id: str = ""  # provenance: the slide it was read from
    quote: str = ""  # the sentence, when the document stated it in so many words
    # --- the read-permission mirror, identical to GraphClaim's (see there) ---
    collection_visibility: str = ""
    collection_read_meta: list[str] = []
    collection_read_content: list[str] = []
    collection_created_by: str = ""
    doc_visibility: str = ""
    doc_read_meta: list[str] = []
    doc_read_content: list[str] = []
