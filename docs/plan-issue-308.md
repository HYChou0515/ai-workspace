# Plan — #308 per-doc permission override

SourceDoc-level `Permission` that **tightens** the read access a document
inherits from its parent collection. Follow-up to #303 (SourceDoc read access
inherits its collection's visibility); part of the #262 permission lineage.

Status: planned (grilled). Depends on #303 (landed). Low priority per the issue,
but the enforcement correctness is the whole value, so v1 does it properly.

---

## 1. Decisions (grilled) & rejected alternatives

| # | Decision | Rejected | Why |
|---|----------|----------|-----|
| D1 | **Intersect semantics — a doc override can only make read access *stricter*, never looser.** Effective read = `collection allows` **AND** `doc override allows`. | *Whole-permission replace* (doc perm fully substitutes, can also loosen); *per-verb fallback*. | The issue's "更鬆 (looser)" is **wrong**. Intersect kills every looser-case footgun: no doc shared to someone who can't read the collection, no fan-out clobbering an override. Reuses the single `authorize()` decision point as an AND of two calls. |
| D2 | **Read-only override for v1** — covers `read_meta` + `read_content` only. Who can edit / add / converse stays governed purely by the collection. | All verbs. | #308 is a #303 *read-inheritance* follow-up; the change lands exactly on the two read paths we already touch. `authorize()` is verb-agnostic so write override is a cheap later increment. |
| D3 | **Representation = reuse the full `Permission | None` struct** on `SourceDoc`, alongside the existing `collection_*` mirror. v1 only honours the read fields; other verbs are stored-but-inert (forward-compat). | Narrow bespoke struct. | Type-consistent with Collection/WorkItem/KbChat; `authorize()` eats it directly; FE/serialization get no special case. Cost = two extra indexed fields. |
| D4 | **Only the collection owner (+ superuser) can set/clear a doc override.** Gate compares `actor.user_id == doc.collection_created_by` (the existing mirror field). | Owner *or* doc uploader; anyone with `change_permission` on the collection. | Cheapest (mirror compare, no collection load) and cleanest read semantics — the setter is the collection authority, so the owner always sees the doc (no "owner locked out of own collection" asymmetry the uploader-can-hide option forces). Widening to the `change_permission` grant later is a one-line swap to `_authorize_collection`. |
| D5 | **Read resolution** (derived from D1+D4): effective read = `authorize(collection mirror, created_by=collection_owner)` AND `authorize(doc.permission, created_by=collection_owner)`. Owner & superuser bypass **both**; the doc uploader gets **no** special read right. | — | Direct consequence of D1/D4. |
| D6 | **AI retrieval path is enforced at the source via a denylist**, not a chunk mirror. Compute the (usually empty) set of overridden docs the speaker can't read; exclude those `source_doc_id`s from the retriever's chunk scope. Short-circuit the whole thing behind a per-collection `has_doc_overrides` flag. | Mirror override read-fields onto every `DocChunk`; filter *after* retrieval; leave the AI path unguarded (v1). | Without this, a doc tightened away from speaker X still leaks via `kb_search` → answer synthesis — the "stricter" would be fake. Denylist keeps `SourceDoc` the single source of truth (no fan-out, no write amplification on the largest table). Post-retrieval filtering wastes retrieval budget & shrinks top-k. |
| D7 | **Doc override grant lists support `group:` subjects.** Wire `_groups_provider(spec)` into `source_doc_access_scope` (closes a pre-existing #303 gap where doc storage-scope ignored groups). | `user:` only in v1. | Consistency with collections; intersect keeps it safe; and it fixes the existing gap. |
| D8 | **Write surface = follow the collection pattern.** Dedicated `PUT /kb/documents/{id}/permission` (body `Permission` or `null` to clear) + a `SourceDoc` permission checker that gates the auto-CRUD `permission`-field write on the same owner-only rule (anti-bypass). Maintain `has_doc_overrides` on set/clear. | No auto-CRUD guard; backend-only, no endpoint. | Without the checker the owner gate is bypassable via `PUT /source-doc/{id}` by anyone with `write_meta`. Mirrors `set_collection_permission` + `CollectionPermissionChecker` exactly. |
| D9 | **v1 includes FE** — reuse the #310 generic role-based permission dialog on the doc (doc list / doc IDE), in a read-only-roles mode, pointed at the doc endpoint. | Backend + API only, FE deferred. | The #310 dialog is generic; wiring it to docs is cheap and makes the feature usable end-to-end. |
| D10 | **No migration.** New fields default to "no override" / count 0, which is the correct semantics for every existing row. | `rm.migrate` backfill. | Unlike a normal specstar index add, `null` visibility **is** the intended meaning (no override), so old rows are already correct. |

---

## 2. Model / data changes

### `SourceDoc` (`resources/kb.py` ~line 308)
- Add `permission: Permission | None = None` — the doc's own override (distinct
  from the `collection_*` mirror, which stays as the inherited default).
- Index `permission.visibility` + `permission.read_meta` (register in
  `resources/__init__.py` alongside the existing mirror indexes ~line 316-320).
- Self-contained: this field is **never** touched by the collection→doc mirror
  fan-out (`push_mirror_to_docs`), which only writes `collection_*`.

### `Collection` (`resources/kb.py` ~line 157)
- Add `has_doc_overrides: int = 0` (count of docs in the collection carrying an
  override). Maintained CAS on override set (+1) / clear (−1). Drives the D6
  short-circuit. Default 0 → correct for all existing collections.

---

## 3. Enforcement points (read only, D2/D5)

Three seams, all **additive** (each ANDs a doc-side check onto an existing
collection-side check; non-override docs are unaffected):

1. **Storage-scope 404 layer** — `source_doc_access_scope`
   (`perm/scope.py` ~line 93): becomes
   `collection_mirror_scope AND doc_override_scope`, both built from the shared
   `_visibility_scope`. A doc with `permission is None` → its `visibility` index
   is null → `_visibility_scope` treats null as public → the AND collapses back
   to the collection scope (zero effect for non-users). Inject `_groups_provider`
   here (D7).

2. **Route-guarded content reads 403 layer** — `render_document`
   (`api/kb_routes.py` ~line 1440), `list_documents` (~1304), chunks/export
   (~931/952/965/989): after the existing
   `_authorize_collection(doc.collection_id, "read_content")`, add
   `authorize(actor, "read_content", doc.permission, created_by=doc.collection_created_by, superusers=...)`.
   Both must pass.

3. **AI retrieval path** — see §4.

---

## 4. AI retrieval enforcement (D6)

`kb_search` / the retriever currently scope by `collection_id` only and do **no**
per-doc read check (enforcement is upstream at chat `converse` / #305 transitive).
Add the doc-side gate at the source:

**Step 1 — resolve `denied_doc_ids`** (shared helper
`denied_doc_ids(spec, actor, collection_ids)`, called where the speaker `Actor`
exists — `kb_search_impl` / KB context):
```python
# short-circuit: skip entirely if no queried collection has overrides
if not any(has_doc_overrides for the queried collections):   # read from #305's
    return frozenset()                                        # already-loaded records if possible
overridden = search_resources(SourceDoc,
    QB["permission.visibility"].is_not_null() & QB["collection_id"].in_(collection_ids))
return { d.id for d in overridden
         if not authorize(actor, "read_content", d.permission,
                          created_by=d.collection_created_by, superusers=...) }
```

**Step 2 — thread into the retriever**: `retriever.search(collection_ids, ...,
exclude_doc_ids=denied)`. The retriever stays permission-agnostic (mechanism, not
policy).

**Step 3 — apply in both scan paths** (`kb/retriever.py` dense ~line 550, BM25
~line 572, via `_scoped`):
```python
scope = _scoped(QB["collection_id"].in_(collection_ids), location)
if exclude_doc_ids:
    scope = scope & QB["source_doc_id"].not_in(exclude_doc_ids)
```
Nothing denied ever reaches RRF/MMR/parent-merge/answer.

**Performance for non-users**: the expensive vector+BM25 query is **byte-for-byte
unchanged** (the `not_in` is only added when `denied` is non-empty). The only
possible add is one empty-returning indexed pre-query, and the `has_doc_overrides`
short-circuit removes even that — provably zero extra cost when nobody in the
queried collections uses the feature.

**`ask_knowledge_base`**: the KB sub-agent already carries the original speaker
(#305), so its retriever gets the same exclusion with no extra wiring.

---

## 5. Write API + anti-bypass (D8)

- `PUT /kb/documents/{id}/permission` (mirrors `set_collection_permission`,
  `api/kb_routes.py` ~line 708): body `Permission | null`; `null` clears →
  reverts to pure inheritance. Owner-only gate (compare `doc.collection_created_by`)
  + superuser. On transition `None→Permission` bump `has_doc_overrides` +1;
  `Permission→None` −1 (CAS on the collection).
- `SourceDocPermissionChecker` (mirrors `CollectionPermissionChecker`,
  `perm/checker.py`): gate the auto-CRUD `permission`-field write on the same
  owner-only rule so `PUT /source-doc/{id}` can't bypass the endpoint. Attach via
  the model's `event_handlers` slot.

## 6. FE (D9)

- Reuse the #310 generic role-based permission dialog, read-only-roles mode,
  invoked from the doc row / doc IDE, pointed at `PUT /kb/documents/{id}/permission`.
- TanStack Query: writes invalidate the doc + doc-list queries.

## 7. No migration (D10)

New fields default to no-override / count 0 = correct for every existing row.
No `rm.migrate`.

---

## 8. Phase breakdown (flat integers, TDD; commit per phase)

- **P1** — `SourceDoc.permission` field + indexes; `Collection.has_doc_overrides`.
  Model + registration only. Tests: struct/serialization, index registration.
- **P2** — Read resolution core: `source_doc_access_scope` = collection AND
  doc-override (intersect, D5), `_groups_provider` wired in (D7). Tests: 404
  storage-scope for stricter doc / group grant / owner & superuser bypass /
  non-override unaffected.
- **P3** — Route-guard content reads intersect (`render_document`,
  `list_documents`, chunks, export). Tests: 403/404 for a tightened doc; owner
  still reads; non-override unchanged.
- **P4** — Write API + anti-bypass: `PUT /kb/documents/{id}/permission`,
  `SourceDocPermissionChecker`, `has_doc_overrides` maintenance. Tests: owner
  sets/clears; stranger 403; auto-CRUD bypass blocked; counter transitions.
- **P5** — AI path denylist + `has_doc_overrides` short-circuit
  (`denied_doc_ids` helper, retriever `exclude_doc_ids`, both scan paths),
  incl. `ask_knowledge_base`. Tests: speaker can't retrieve a tightened doc's
  chunks; short-circuit skips the query when no overrides; non-user retrieval
  identical.
- **P6** — FE: reuse #310 dialog on docs (read-only mode) + query invalidation.
  vitest per FE TDD.

Each phase red→green→refactor, committed separately. 100% coverage gate on the
full local suite at the end.

## 9. Out of scope
- Write / converse / execute per-doc override (later integer phase if needed).
- Loosening (explicitly rejected — D1).
- per-doc override on WorkItem / KbChat (this is SourceDoc-only).
