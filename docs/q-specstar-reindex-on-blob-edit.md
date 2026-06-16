# Question: triggering a derived-data rebuild when a resource's blob is patched

I'm using SpecStar (0.11.6). I have a resource whose **content is a `Binary`
blob**, and a set of **derived rows** (search chunks) computed from that blob by
a background worker. When a user **edits the content**, I want the derived rows
rebuilt — the SpecStar-native way, without a custom write endpoint.

I've worked out the blob-patch part (upload immutable blob → CAS-`PATCH` the
resource's `content` reference under `If-Match`). My open question is **how to
wire the rebuild trigger** as an event handler, given two wrinkles below.

## The model + the indexing flow

```python
class SourceDoc(Struct):           # resource "source-doc"
    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    path: str
    content: Binary                # the editable bytes (content-addressed blob)
    text: str | None = None        # derived/extracted text (None ⇒ decode content)
    status: str = "ready"          # "indexing" | "ready" | "error"
    status_detail: str = ""
```

```python
# resources/__init__.py  (runs inside make_spec, at app construction)
spec.add_model(SourceDoc, indexed_fields=["collection_id"])
```

A durable queue + background consumer does the heavy work. The producer:

```python
class IndexCoordinator:
    def enqueue(self, doc_id: str, collection_id: str) -> None:
        self._job_rm.create(IndexJob(payload=IndexJobPayload(doc_id, collection_id)))

    def _handle(self, job) -> None:           # runs in the consumer thread
        doc_rm = self._spec.get_resource_manager(SourceDoc)
        with doc_rm.using(user=...):
            self._ingestor.index(doc_id, source_doc_rm=doc_rm)
```

Crucially, `ingestor.index()` **writes the doc back** at the end via
`rm.update(doc_id, replace(doc, status="ready"))` (and the manual reindex route
also does `rm.update(..., status="indexing")` before enqueueing). So the doc is
mutated *as part of* indexing.

The FE edit (already designed) is:

```
POST /blobs/upload                       → { file_id, size, content_type }
PATCH /source-doc/{id}  (If-Match: etag)  body { content: { file_id, ... } }
```

i.e. content edits arrive as **`PATCH`**, while the index worker / reindex route
write via **`rm.update`**.

## What I want

Register an event handler on `SourceDoc` so that **a content edit enqueues a
reindex automatically** — no custom edit endpoint, the framework does it.

```python
# conceptually:
do(on_doc_content_patched).on_success(ResourceAction.patch)
# handler body:
def on_doc_content_patched(ctx: OnSuccessPatch) -> None:
    # only when the patch actually touched /content (not metadata-only patches)
    if touches(ctx.patch_data, "/content"):
        index_coordinator.enqueue(ctx.resource_id, <collection_id>)
```

## Wrinkle 1 — avoiding a rebuild loop

If I'd hooked `on_success(update)`, the worker's own `rm.update(status="ready")`
would re-fire the handler → re-enqueue → infinite reindex.

My plan is to scope the handler to **`ResourceAction.patch` only**, since:

- the user edit is an HTTP `PATCH` → `ResourceAction.patch`
- the worker / reindex writes are `rm.update(...)` → `ResourceAction.update`

so the handler never sees the worker's writes, and the loop can't form.

**Q1.** Is this the right way to break the loop? Specifically:

1. Does `rm.update(...)` dispatch **only** `ResourceAction.update` events (never
   a `patch` event)? i.e. is a handler scoped to `on_success(patch)` guaranteed
   not to fire on `rm.update`?
2. Do **both** PATCH flavors over HTTP — RFC 6902 (json-patch, `application/
   json-patch+json`) and RFC 7396 (merge-patch, `application/merge-patch+json`)
   — dispatch as `ResourceAction.patch`? Or does merge-patch lower to an
   `update`/`modify` internally (which would change which action I must hook)?
3. `OnSuccessPatch` carries `patch_data: JsonPatch`. For a merge-patch request,
   is `patch_data` still populated as a JsonPatch I can inspect to see whether
   `/content` was among the changed paths? Or should I detect "content changed"
   a different way (e.g. compare the new revision's `content.file_id` against the
   previous revision)?

## Wrinkle 2 — the handler needs a dependency built *after* `add_model`

`event_handlers=[...]` is an **`add_model`-time** parameter, but my handler needs
`index_coordinator`, which is constructed **later** (it itself calls
`spec.add_model(IndexJob, ...)`, so it must run after the spec exists). So at the
moment I register `SourceDoc` I don't yet have the coordinator to close over.

**Q2.** What's the idiomatic SpecStar way to wire a handler whose collaborator
is built after the model is registered? Candidates I'm considering:

- **(a) Post-registration API** — is there something like
  `spec.get_resource_manager(SourceDoc).add_event_handler(...)` or
  `spec.register_event_handler(SourceDoc, ...)` to attach a handler *after*
  `add_model`? (If so, I'd attach it right after building the coordinator.)
- **(b) Lazy closure** — register at `add_model` time a `SimpleEventHandler`
  whose function resolves the coordinator from a small mutable holder that's
  filled in once the coordinator exists.
- **(c) `StringRefEventHandler`** — register a dotted `"module.fn"` ref that, at
  fire time, looks the coordinator up from app state / a registry.
- **(d) Reorder construction** so the coordinator (and thus the closure) exists
  *before* `add_model(SourceDoc)`.

Which of these is the recommended pattern? Is there a first-class way to express
"this handler depends on a runtime singleton" that I'm missing?

## What runs the handler, and when

A couple of semantics I want to confirm so the enqueue is safe:

**Q3.** The `on_success` handler runs **after** the patch revision is committed,
so inside the handler `rm.get(resource_id)` returns the **new** revision
(letting me read `collection_id`), correct? And the handler runs
**synchronously** in the request's call stack — so I must keep it light
(`enqueue` is a single `create`), and any exception it raises propagates to the
PATCH caller (i.e. I should guard it). Is that the right mental model, or do
handlers run out-of-band?

## Versions / constraints

- SpecStar 0.11.6; msgspec structs; blobs content-addressed (default), reclaimed
  by `collect_orphans()`.
- I do **not** want a custom edit endpoint; content writes should go through the
  auto-CRUD `PATCH /source-doc/{id}`.
- The rebuild must be idempotent and must not wedge the queue (the worker already
  marks `status="error"` on failure and returns normally).
