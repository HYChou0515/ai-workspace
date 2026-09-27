# Plan — #219 workspace filestore: inline bytes → specstar `Binary`/blob store

> 被 #492（plan-issue-492.md）推翻

Migrate the workspace FileStore off inline `dict[str, bytes]` (one record per
workspace) onto per-file `Binary` resources backed by the specstar blob store,
and rebuild file **upload** into a real "convenient upload" feature (folders,
big files, progress, drag-drop) — backed by end-to-end streaming so a GB upload
never materialises in RAM.

Locked via `/grill-me`. Companion issue **#245** (per-workspace total quota +
blob GC) is explicitly out of scope here.

## Why

`SpecstarFileStore` keeps every file of a workspace inline in one
`_WorkspaceFiles.files: dict[str, bytes]` record (`filestore/specstar_impl.py`).
It is the last store still on inline bytes (KB `SourceDoc` / wiki `WikiPage`
moved to `Binary` long ago). Consequences: O(whole-workspace) read-modify-write
per file write, O(n²) folder upload, no big files, a silent 10 MB reverse-sync
cap that drops files. **prod runs `filestore.kind: specstar`** (memory is
test-only), so this is a live problem with real persisted data.

## Locked decisions

**Storage**
- New specstar model `WorkspaceFile` — **one resource per file**, `content:
  Binary`. `SpecstarFileStore` rewritten over it (read = `restore_binary` of one
  file; write = create/`modify` one resource; `ls` = indexed query on
  `workspace_id`). `Binary` restore is **eager** (bytes pulled on
  `restore_binary`), so per-file makes "load one file = one file's bytes"
  structural.
- **Empty dirs**: keep the existing semantics (deleting a file leaves parent
  dirs intact). Dirs are pure small strings (no bytes), so they stay in a small
  per-workspace dir record — the bytes bottleneck is solved on the per-file
  side; dir structure is untouched.
- **move/copy → metadata only**: a new `WorkspaceFile` pointing at the same
  content `file_id` (blob is content-addressed) — zero bytes moved, zero RAM.
- CAS hooks / CRUD-route leak: **dropped** — current store has no CAS today
  (status quo, no regression); prod already exposes `_WorkspaceFiles` routes via
  specstar, so renaming to `WorkspaceFile` is route-neutral.

**Streaming (top constraint: never OOM)**
- Upload endpoint streams `request.stream()` → a host **temp file** (lands on
  disk, never whole-in-RAM) → blob upload-session → `finalize` → `Binary`.
  **FE `writeFile` is unchanged** (still one PUT of a Blob).
- Sandbox protocol gains path-based **`upload_file(handle, local_path,
  remote_path)`** + **`download_to_file(handle, remote_path, local_path)`**
  (impl for Local / Docker / Mock). Warm upload = temp file → blob (durable) +
  `upload_file` into the live container (visible) + seed the mirror version so
  the next mirror won't re-`download` it. Mirror uses `download_to_file`. ⇒
  warm/cold, upload + agent-generated big files all stream, no RAM spike.
- Download: big files stream out via specstar `GET /blobs/{file_id}` instead of
  reading the whole file into a `Response`.

**Cap**: configurable single-file cap, default ~2 GB (`filestore.max_file_size`);
`sync/ignore.py` mirror cap aligns to it (no longer a hard 10 MB). Per-workspace
total quota + blob GC → **#245**.

**attach feature**
- Chat composer 📎 → pick file(s) → upload to `uploads/<name>` (folder picks
  preserve structure; v1 fixed `uploads/` root) → **send is disabled until the
  upload completes** → user writes/edits the prompt → send.
- On send the FE prepends `Attached: /uploads/<name>` line(s) to the message
  text; the agent reads them with the existing `read_file` tool (no new context
  plumbing). Replaces the old attach.

**Migration**: one-time transform of existing `_WorkspaceFiles` inline-bytes
records → per-file `WorkspaceFile` + blobs + dir record. Deploy-safe (must land
before P1's model change is deployed).

**Out of scope**: resume-after-disconnect upload; total quota + blob GC (#245).

## Phases (flat)

Delivered in this PR (P1–P6 core):

- **P1** ✅ — `WorkspaceFile` model + rewrite `SpecstarFileStore` over per-file
  `Binary` (read/write/ls/exists/delete; dirs via a small per-workspace
  `_WorkspaceDirs` record). FileStore protocol surface preserved; the 23
  existing filestore tests pass unchanged.
- **P2** ✅ — one-time `migrate_inline_to_binary` (old inline bytes → new shape),
  wired as an idempotent boot step for the specstar filestore.
- **P3** ✅ — streaming upload: `FileStore.write_from_path` (temp-file → blob
  upload-session → finalize) + the `PUT /files/{path}` endpoint streams
  `request.stream()` to a staging file. No whole-upload-in-RAM.
- **P4** ✅ — Sandbox protocol `upload_file` / `download_to_file` (Local / Docker
  / Mock / Http) + facade `write_from_path` / `read_to_file` routing (warm →
  sandbox, cold → blob) + `SandboxSync` restore/mirror stream through a staging
  file. Warm uploads land in the sandbox; durability follows on the next mirror
  (same invariant as any warm write). **Simplification vs the original plan**:
  no "dual-write + seed version" — warm→sandbox + streaming mirror is enough and
  matches the existing model.
- **P5** ✅ — configurable single-file cap (`filestore.max_file_size`, default
  ~2 GB; enforced mid-stream → 413) + `sync/ignore.py` aligns its reverse-sync
  cap to it.
- **P6** ✅ (minimal) — FE: drop the FileTree 8 MB client cap so big files
  actually upload; graceful error when the server rejects an over-size upload.

Deferred (not in this PR — the storage migration + OOM-safety is self-contained;
these are FE-feature / download-side layers on top):

- **Download-side streaming** — `read_file` still reads whole bytes (it's the IDE
  viewer, mostly small/text; the warm path is whole-bytes anyway). Big-file
  download streaming via `/blobs` is a follow-up.
- **Richer P6** — `writeFile` → XHR upload progress; drag-drop overlay.
- **P7 attach UX** — chat-composer 📎 (upload to `uploads/`, send disabled until
  done, path injection on send) replacing the old attach.
- **Per-workspace total quota + blob GC** — tracked in **#245**.

## Touch map

- `src/workspace_app/resources/` — new `WorkspaceFile`
- `src/workspace_app/filestore/` — `specstar_impl.py` rewrite, `protocol.py`
  (optional `write_stream`)
- `src/workspace_app/sync/` — `sandbox_sync.py` (streaming mirror), `ignore.py`
  (cap)
- `src/workspace_app/sandbox/` — `protocol.py`, `local_process.py`, `docker.py`,
  `mock.py` (`upload_file` / `download_to_file`)
- `src/workspace_app/api/app.py` — files PUT (streaming) + GET (streaming
  download)
- `web/src/pages/investigation/FileTree.tsx`, `web/src/api/real.ts`
  (`writeFile` → XHR + progress), chat composer (attach)
