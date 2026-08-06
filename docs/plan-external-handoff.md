# Plan — External-system handoff into an App work item (no new concepts)

## Problem

A legacy analysis site (the old RCA web app) wants a button that hands its finished
analysis over to this platform's RCA app, so the agent can carry the investigation
forward. The naive shape is "button → `POST /a/rca/items`", and that is exactly the
failure the owners are worried about: `create_app_item` mints
`f"{rm.resource_name}:{uuid.uuid4()}"` on every call (`api/item_routes.py`), so it is
**non-idempotent by construction**. Press it five times for one real-world problem and
you get five work items, each holding a fifth of the context — the handoff destroys the
very continuity it exists to provide.

The obvious cure — derive a dedupe key from the source and reuse the item it maps to —
**does not apply here**. The legacy side splits one real problem into several separate
analyses and carries **no field that links them** (established during grilling; there is
no case number, no work order, nothing). The grouping exists only in a person's head.
So automatic convergence is impossible, and "which item does this belong to" can only be
answered by the person pressing the button.

That reframes the job: the platform's task is not to *decide* the grouping but to *make
the human's decision cheap and repeatable* — show them what already exists, and remember
what has already been handed over so the same analysis is not imported twice.

## What this is deliberately NOT

There is **no "import" concept** in this design. Broken into pieces, the request contains
three things, and two of them already have names here:

| The thing | What it already is |
|---|---|
| Put files in at creation time | **seeding** — `apps/seeding.py::seed_item`, how every profile's starter content arrives |
| Put files into an existing item | **writing a file** — `PUT /a/{slug}/items/{id}/files/{path}` (already streaming) |
| Remember which external records this item already absorbed | **nothing yet — this is the only gap** |

So this plan adds **one field** and **no endpoints**. No `/handoff` route, no
`import_path`, no `/imports/` directory, no new vocabulary for reviewers to learn. The
legacy site composes the flow out of routes that already exist.

## Goal

A legacy system can hand an analysis to any App's work item — creating a new one or
adding to one the user picks — and the platform remembers what it absorbed, so a second
press against the same item is a no-op the caller can detect **before** uploading a
single byte.

Non-goals: automatic grouping (impossible, see above); any UI on this side (the picker
lives in the legacy page); server-side search/pagination of the item list (the caller
pages client-side — see Phase 1 rationale).

## Locked decisions

Established by grilling; each replaced a plausible alternative that is recorded under
[Rejected alternatives](#rejected-alternatives).

| # | Decision |
|---|---|
| 1 | **No automatic matching.** The user picks the target item (or creates one) in the legacy picker. |
| 2 | **Many-to-one.** Several legacy analyses converge onto one work item. |
| 3 | **Idempotency key = (item, external ref).** Same ref already on that item ⇒ do nothing. |
| 4 | **The same ref MAY appear on several items.** Per-item uniqueness only; not globally unique. |
| 5 | **Identity comes from the shared login.** The legacy *browser* calls us, so the session rides along; nothing carries a user id in a parameter. |
| 6 | **Files travel by upload**, through the existing streaming `PUT`. The default workspace quota is 20 GB (`config/schema.py`), not a constraint at MB scale — though a deployment may have lowered it. |
| 7 | **Item created first, files written, ref recorded LAST, then the browser navigates.** Not one combined multipart create — and never `external_refs` at create time, or a handoff that dies mid-upload leaves an item claiming an analysis it does not hold, which the picker then greys out so it can never be retried. |
| 8 | **Nothing runs on arrival.** No auto-sent turn, no prefilled prompt, no edit to `/brief.md`. |
| 9 | **Permission is passed explicitly as `public`** by the caller, overriding the private-by-default. To be tightened later, once the flow is proven in production. |
| 10 | **Generic, not RCA-specific.** No `slug` is special-cased; the field lives on `WorkItemBase`. |
| 11 | **The ref field is NOT indexed.** The caller fetches a page of items and filters client-side, so no server-side query exists to justify an index. |
| 12 | **The caller fetches the newest 100 items** and pages further itself if it needs to. |

### Why decision 11 has teeth

Not indexing is not merely "skipping an optimisation" — it **forbids a query**. But the
first version of this section got the mechanism backwards, and the wrong reason shipped
into the caller-facing doc, the field's docstring and a guard test's failure message. It
is corrected here rather than quietly reworded, because the wrong version was persuasive.

**What was claimed:** an un-indexed `.contains` degrades on SQL backends to a substring
`LIKE`, so `"legacy-rca:1"` would match `"legacy-rca:12345"`, staying exact in the
in-memory backend the tests use — green in CI, wrong in production.

**What actually happens** (measured against the running API, two items, one holding
exactly `legacy-rca:1`): the condition never reaches SQL at all. Because the field is in no
`indexed_fields`, specstar strips it in `_validate_query_fields` and emits a
`SpecStarWarning`; the query returns **`200` with zero rows**, identically on memory and
SQL, since the stripping happens before any SQL is generated. The control — the same query
shape over the indexed `severity` — returns both rows, so the query machinery is fine.

The `CLAUDE.md` note the plan leaned on describes the *opposite* precondition: that trap
needs the field to **be** in `indexed_fields` while having lost its `list[...]`
annotation. Ours is annotated `list[str]`, so indexing it would auto-register a list field
and `.contains` would be exact — **indexing would make a query correct, not dangerous**.

The conclusion survives, for a better reason. Zero rows is the worst possible answer to the
only question this field exists to settle: "which items already absorbed record X?"
answered with nothing reads as "none", so the caller re-imports and creates the duplicate
item this design exists to prevent — with an unread warning as the sole trace. So: **no
code may query this field**; the client filters the page it already fetched. The field
stays un-indexed because there is no server-side query to justify the write-time cost — not
because indexing is unsafe. Two guards hold the line together (no App indexes it; no module
mentions it outside its definition), and if a genuine query need ever appears the honest
move is to index it deliberately and delete both guards, not to smuggle a filter past them.

### Why decision 12 needs `updated_time`, not `created_time`

Capping the list re-opens the original wound from a side door: if the target item has
fallen past the cap, the user cannot see it, so they create a new one — item sprawl again.
Sorting by **`updated_time` descending** greatly reduces it, at zero cost: recording a
handoff bumps the timestamp, so each successive handoff returns that item to the front —
which covers the main case, "one problem handed over several times".

**Corrected from the original claim, which was measurably too generous.** It said
"absorbing a handoff writes files *and* appends a ref, which bumps the timestamp, so an
item that is being actively worked is pushed back to the front". Only the ref append does
it. Measured: uploading a file into the older of two items left the order unchanged;
appending a ref to it moved it to the front. `updated_time` belongs to the item row, and
only `item_routes.py` and the generic CRUD patch write it — files live in the FileStore and
chat turns write `Conversation`, so neither touches it. So an item created by hand and
worked in daily, never handed to from outside, sinks exactly as it would under
`created_time`. For the caller's own flow this is fine (everything it touches has been
handed to), but the sentence "actively worked items stay at the front" is not true in
general and should not be relied on. Both helpers are specstar built-ins
(`QB.created_time()` / `QB.updated_time()`, `specstar/query.py`), so **no index is needed
for the sort either** — the same pattern already in use at `api/kb_routes.py:2043`.

## Interface contract (what the legacy site calls)

Everything below already exists except the field in step 1's response.

**1 — List candidates.** specstar's generated CRUD, newest first, capped. The
exact shape below is what the routes actually accept — verified against a running
app, not inferred; the first draft of this plan guessed `GET /{model}?sort=-updated_time`
and was wrong on both the path and the parameter:

```
GET /api/rca-investigation
      ?limit=100
      &sorts=[{"type":"meta","key":"updated_time","direction":"-"},
              {"type":"meta","key":"resource_id","direction":"+"}]
```

Two corrections that arrived from review, both of which had shipped in the
caller-facing doc:

- It must be the **envelope** listing, not `/data`. `/data` returns the struct
  alone — **no resource id** — so a picker built on it can render the rows and
  then act on none of them, since every later step keys off the id. The id is at
  `revision_info.resource_id`, which is where this platform's own frontend reads
  it from (`web/src/api/real.ts`). The contract double missed this because it
  only ever consumed `title` from the listing and took the id from the create
  response — it never played the one move the integration depends on.
- The sort needs a **tiebreaker**. `updated_time` alone is not a total order, so
  two rows sharing a timestamp may swap between pages and `offset` paging drops
  one. `api/kb_routes.py` already pairs its sort with `resource_id` for exactly
  this reason (#184); the plan cited that line as precedent while omitting the
  precaution it exists to document.

Each returned record carries its own `external_refs`, because non-indexed fields are still
part of the record. The picker therefore decides everything locally: which items exist,
which of them already absorbed this analysis (grey those out — this is decision 3, resolved
without a single extra request), and how to sort or filter them for display.

> **Trap:** specstar's default `limit` is a sentinel (~4.29e9), **not** a page size. Omit
> `limit` and the call silently degrades to fetching everything, nullifying decision 12.

**2a — User picked an existing item:** upload each file (raw body, not multipart;
`204` on success), then append the ref.

```
PUT   /api/a/{slug}/items/{id}/files/{path}
PATCH /api/rca-investigation/{id}
      [{"op": "add", "path": "/external_refs/-", "value": "legacy-rca:12345"}]
```

**2b — User wants a new item:** create it, then upload, then append as above.

```
POST /api/a/{slug}/items
     { ...app fields..., "external_refs": ["legacy-rca:12345"],
       "permission": {"visibility": "public"} }
```

**3 — Navigate** the browser to `/a/{slug}/{itemId}`. Files are already in place, so the
user never sees a half-populated workspace.

The caller-facing version of all this is [`external-handoff.md`](external-handoff.md).

**Ref format:** `<system>:<record-id>`, e.g. `legacy-rca:12345`. Opaque to the platform —
never parsed, only compared.

**File layout:** the platform imposes none. The contract doc will *recommend*
`<system>-<record-id>/` as a path prefix so several handoffs into one item cannot collide,
but that is guidance to the caller, not a rule we enforce.

## Phases

Flat integers, one commit each, TDD throughout (`/tdd`): every phase starts from a test
that fails for the stated reason.

### Phase 1 — `external_refs` on `WorkItemBase`

Add `external_refs: list[str] = []` as a Tier 1 field (a platform capability every App
gets, like `env_vars` — decision 10), deliberately **absent** from `INDEXED_FIELDS`.

Red tests:

1. `POST /a/{slug}/items` with `external_refs` → reading the item back returns them.
2. A listed record carries `external_refs` — without this the client-side filter of
   decision 11 has nothing to filter on, so this test *is* the design.
3. `PATCH` with `{"op":"add","path":"/external_refs/-",...}` **appends**; a pre-existing
   ref survives. (JSON Patch is RFC 6902 here — `crud/route_templates/patch.py` — so the
   payload is a diff, not a whole-record replace. This is materially safer than the
   `#587` class of bug, but the guarantee needs a test, not an assumption.)
4. Two concurrent appends of *different* refs to one item: both survive. This is the one
   test that can genuinely fail — if specstar's patch is read-modify-write without
   row-level protection, one append is lost. If it fails, the fix is a retry-on-revision
   loop in the caller contract, documented in Phase 3; **do not** paper over it by making
   the client send the whole list, which reintroduces last-writer-wins.

   **Outcome (this prediction was right, and the first version of the test hid it).**
   The original test used `asyncio.gather`, which cannot fail here: the patch route's
   critical section is synchronous, so two coroutines on one event loop never interleave.
   It recorded a false pass and the Phase 3 follow-up was never triggered. Re-run with
   threads, 8 concurrent appends lose 2–3 refs **with every request returning 200**.

   The retry-on-revision loop is now in the contract and measurably helps, but it does
   **not** close the hole: `expected_revision_id` is enforced in
   `specstar/resource_manager/core.py` as check-then-write with no transaction spanning
   the comparison and the write, so two writers can both pass the check. Measured after
   the fix: 5 runs → 2 pass, 3 still lose a ref. Single-pod is unaffected (one event loop
   serialises the synchronous section); multi-pod, the documented production shape, is
   not. The test stays in the suite as a non-strict `xfail` so it announces itself the day
   the upstream primitive becomes atomic. **This belongs upstream in specstar** — there is
   no correct fix at this layer, because the only cross-pod primitive available is the
   non-atomic CAS itself.

   The retry loop still earns its place for a second reason: it re-reads before writing and
   returns early when the ref is already present, which makes the whole call idempotent.
   Without that, a caller that times out on a request which actually landed — the ordinary
   thing to do — records the ref twice, and "at most once per item" was until now a rule
   nothing enforced.

No migration: the field is new, so every existing row's correct value is the empty list,
and nothing queries it.

### Phase 2 — CORS, configurable

The legacy browser cannot call us today — there is no `CORSMiddleware` anywhere in
`api/app.py`. Add `server.cors_allowed_origins: list[str] = []` to the config schema and
mount the middleware **only when the list is non-empty**, so no existing deployment or
test changes behaviour.

Red tests:

1. Empty config → no CORS headers (default deployments unchanged).
2. Configured origin → preflight `OPTIONS` returns that origin and
   `Access-Control-Allow-Credentials: true`.
3. Unlisted origin → no allow header.

`allow_credentials=True` is mandatory (decision 5 rides on the session cookie) and
browsers forbid pairing it with `*`, so origins must be listed explicitly. Ship the
default empty and let the operators fill in the legacy hostname at deploy time.

### Phase 3 — Integration contract doc

`docs/external-handoff.md` (added to the mkdocs nav), addressed to the *calling* team:
the four steps above, the ref format, the recommended path prefix, and the two traps
promoted to prominence — the `limit` sentinel, and the fact that `permission` must be
sent explicitly or the item is born private and invisible to everyone else, which
silently defeats the whole convergence goal.

### Phase 4 — End-to-end proof, from the caller's side

This phase exists because **nothing in this work is visible in our own UI** — the picker
lives in the legacy page, so "it merged" proves nothing about whether it works. The
deliverable is an integration test that plays the legacy site's part: list → create with
refs and public permission → upload two files → append a ref → assert the second press
against the same item is detectable as already-absorbed → assert the workspace file tree
shows the files under the recommended prefix.

Per the project's contract-double rule, this must model **the other side's actual request
sequence**, not merely assert that our handlers do not crash. A test that only says "we
did not do anything wrong" is immune to the regressions that matter here.

## Risks and accepted trade-offs

1. **Items beyond the first 100 are invisible to the picker**, so a long-dormant
   investigation can still spawn a duplicate. Mitigated to near-zero by the `updated_time`
   sort (see above); fully solved only by server-side search, which is deliberately out of
   scope.
2. **The same analysis may be attached to several items** (decision 4). Chosen knowingly:
   it buys flexibility at the cost of one analysis's discussion potentially living in two
   places.
3. **`public` (decision 9) is world-WRITABLE, not merely world-readable.** This risk was
   originally written as "visible company-wide", which understates what is being accepted
   and is the sentence a reviewer would rely on when judging whether "tighten it later" is
   tolerable. `perm/authorize.py` grants every verb except `change_permission` under
   `public`. Measured, as a second user against someone else's public item: upload or
   overwrite any file `204`, rewrite the title `200`, **reassign `owner` `200`**; only
   delete is refused (`403`, creator-only). `converse`, `execute` and `use_terminal` come
   with it, so each handoff item is also a shell any employee can drive. Still accepted for
   the rollout — but on those terms, not the softer ones.
4. **Client-side filtering gets heavier as item counts grow.** Known, not an oversight.
   The remedy (index + migrate) is understood; the trigger should be a measurement, not a
   guess.
5. **Concurrent appends** — see Phase 1 test 4. Measured, and it does lose refs; the
   residual hole is upstream and documented there.

6. **The per-user disk quota lands on one person.** Decision 6 reasoned only about
   `filestore.workspace_quota` and missed the second cap: `quota/disk_ledger.py` charges an
   item's bytes to its `owner`, summed across every item in that person's name (#688).
   Convergence is the whole point of this design, so N colleagues' uploads all land in one
   item — created by whoever pressed first — and every one of them is charged to that
   person's budget. When it trips, the uploader gets a `507` naming a quota they cannot see
   and cannot clear, because the space is in other items belonging to someone else. And
   since `owner` is a plain PATCHable field on a `public` item (risk 3), anyone can move
   the debt onto anyone else — which is #687, and is why #687 gates this quota having any
   force at all.

## Rejected alternatives

- **Derive a dedupe key from the legacy side** — no field links the analyses that belong
  to one problem. The premise simply does not hold.
- **A dedicated `/handoff` endpoint (+ `import_path`, `/imports/` layout)** — invented a
  concept the platform does not need. Two of the three underlying actions already have
  names here; the third is a field, not a protocol.
- **One multipart request that creates the item and carries the files** — the
  "already absorbed, do nothing" answer must arrive *before* the upload, or users transfer
  megabytes only to be told it was unnecessary. It would also force `create` from JSON to
  multipart for no gain.
- **Globally unique refs (block attaching to a second item)** — rejected in favour of
  flexibility (decision 4).
- **Move the ref when re-attached elsewhere** — a mis-click silently relocates a
  colleague's data.
- **Auto-start a turn, or prefill the composer, on arrival** — rejected; arrival is silent
  (decision 8).
- **Append an arrival note to `/brief.md`** — rejected by the owner.
- **Index `external_refs`** — no server-side query exists to justify it, and indexing
  carries a migration obligation. See "Why decision 11 has teeth".
- **Fetch every visible item** — replaced by the newest-100 cap (decision 12).
