# Question: efficient aggregates / counts without materializing rows or N+1

I have a few read paths in a SpecStar app that currently **materialize whole
tables and aggregate in Python**, or do **one query per parent (N+1)**. I'd like
to know the idiomatic SpecStar way to express these as pushed-down aggregates,
and what's supported today.

Versions: SpecStar 0.11.6, msgspec structs, the default storage backend.

## The models (trimmed)

```python
class Collection(Struct):       # resource "collection"
    name: str
    ...

class SourceDoc(Struct):        # resource "source-doc"
    collection_id: Annotated[str, Ref("collection", on_delete=cascade)]
    path: str
    content: Binary             # content.size is the blob size
    ...
# spec.add_model(SourceDoc, indexed_fields=["collection_id"])

class CitationEvent(Struct):    # resource "citation-event" (append-only log)
    collection_id: str          # single FK
    document_id: str            # single FK — the cited SourceDoc id
    source_chunk_ids: list[str] # the chunks the [n]'s merged passage spanned
    ...
# spec.add_model(CitationEvent)        # NOTE: no indexed_fields
```

---

## Scenario 1 — per-collection aggregates (N+1 + materialize-all)

The collections grid shows, for every collection, `doc_count`, total `size`
(sum of each doc's blob size), and the latest `updated_time` across its docs.

Today, for **each** collection I materialize **all** its SourceDocs just to
count + sum + max:

```python
def _collection_out(collection):
    count = size = 0
    updated = collection.info.updated_time
    # one query PER collection, and it loads every doc's full data
    for d in doc_rm.list_resources((QB["collection_id"] == collection.id).build()):
        size += d.data.content.size
        count += 1
        updated = max(updated, d.info.updated_time)
    ...

# list_collections() calls _collection_out for EVERY collection → N+1
return [_collection_out(c) for c in coll_rm.list_resources(QB.all())]
```

So N collections ⇒ N `list_resources` calls, each materializing the whole
collection's docs just to produce three numbers.

**Q1.** Is there a pushed-down **aggregate** API — `COUNT` / `SUM(field)` /
`MAX(field)` — over an indexed field, so I can get `count` / `sum(content.size)`
/ `max(updated_time)` for a `collection_id` **without** materializing the rows?
(`count_resources(query)` looks like the COUNT case — does it count at the
storage layer without loading rows? Is there a `SUM`/`MAX` equivalent?)

**Q2.** Can I compute these **grouped by `collection_id` in ONE query** across
all collections (a `GROUP BY`), so the grid is O(1) queries instead of O(N)?
If not group-by, is there a batched form (e.g. aggregate filtered by
`collection_id IN [...]` returning per-key results)?

**Q3.** `content.size` is a field on a `Binary`. Can an aggregate / index reach
a nested blob-metadata field like `content.size`, or do I need to denormalize it
(store a plain `size: int` on the row) to aggregate it?

---

## Scenario 2 — "cited count per doc" over an UN-indexed event table

A doc/collection's "cited N×" badge counts how many `CitationEvent`s reference
it. Today I scan the **entire** CitationEvent table and bucket in Python on
every list call:

```python
def doc_cited(spec) -> dict[str, int]:
    counts = Counter()
    for e in cite_rm.list_resources(QB.all()):     # <-- FULL TABLE SCAN
        counts[e.data.document_id] += 1            # one event = one cited doc
    return counts
# collection_cited is the same over e.collection_id; chunk_cited credits
# +1 to EACH id in e.source_chunk_ids (a list field).
```

This grows unbounded with citation volume, and it's unfiltered (it scans every
collection's events even when rendering one collection's list).

**Q4a (single FK fields).** `document_id` / `collection_id` are single values.
Should I `add_model(CitationEvent, indexed_fields=["document_id", "collection_id"])`
and `count_resources(QB["document_id"] == X)` — and does that push the COUNT down
to the index without materializing rows? But I need the count for **every** doc
on the page, so per-key counting is itself N+1 — is there a **group-by**
(`COUNT(*) GROUP BY document_id`) so one query returns `{document_id: count}`?

**Q4b (list field).** `source_chunk_ids` is a **list**. For the chunk tally I
need "events whose `source_chunk_ids` CONTAINS chunk X", counted per chunk. Can a
field index cover list-membership (`CONTAINS`) so this is countable without a
scan, or do I have to normalize into a per-(event, chunk) row to make it a
plain indexable count?

**Q5.** More generally: for dashboard-style counts/sums that today force a
`list_resources(QB.all())` + Python aggregation, what's the recommended pattern
in SpecStar — index + `count_resources` per key, a group-by aggregate, a
materialized counter I maintain on write, or something else?

---

## What I'm NOT asking about

One related lookup I can already fix myself: a path → document fetch that used
to scan all docs is now a direct `get(encode_doc_id(collection, path))` because
the id is the natural key — so that one's O(1) without any new API. The
questions above are specifically about **counts / sums / max grouped by a key**,
which I don't see how to express without materializing or N+1.

## Why it matters

These are hot read paths (the collections grid + every document list), so I'd
rather push the aggregation into the query/index than load tables into the app
and reduce them in Python. If the answer is "there's no aggregate API yet,
denormalize counters on write," that's a fine answer — I just want to use the
intended pattern rather than the fetch-all one.
