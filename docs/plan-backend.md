# workspace-app — Backend Plan

Living document for backend work. Frontend has its own plan at
[`plan-frontend.md`](./plan-frontend.md) and a different agent owns it.

When backend changes touch the wire (SSE event schema, new HTTP routes,
contract changes), update the **Cross-cutting contracts** section below
so the frontend agent knows what changed.

---

## 1. Done

Backend scaffolding from the eight `/grill-me` steps, plus the
small-model retry path:

| #  | Commit    | Scope                                                    |
|----|-----------|----------------------------------------------------------|
| 1  | `05e2186` | Bootstrap + Sandbox Protocol + MockSandbox                |
| 2  | `9ae9921` | FileStore Protocol + SpecstarFileStore (mode 2)           |
| 3  | `74a3960` | Workspace / AgentConfig / Conversation specstar Structs   |
| 4  | `086e2e1` | Agent tool wrappers (T2: exec + read/write/ls/exists/del) |
| 5  | `d06e3fb` | FastAPI app factory + SSE message endpoint                |
| 6  | `64518d9` | LocalProcessSandbox + DockerSandbox adapters              |
| 7  | `8c8520e` | LitellmAgentRunner (OpenAI Agents SDK + LiteLLM/Ollama)   |
| 8  | `b4f6db0` | React SPA chat page + entrypoint + CLAUDE.md (FE handed off) |
| +  | `261ae64` | LitellmAgentRunner retry-with-feedback for small-model errors |

94 tests / 1 live-skipped on a cold box, 100% coverage (statement +
branch). All `ruff check`/`ruff format`/`ty check` clean.

---

## 2. Not done — locked-in `/grill-me` decisions still unimplemented

Architectural commitments from §Q6/Q10/Q11. The MVP is not honest until
they're in.

- ~~**Q10 / c3 — Interrupt on new user message.**~~ Landed in `48a0fa8`.
- ~~**Q10 / b1 — Idle kill after 15 min.**~~ Landed (this commit).
- ~~**Q11 — FS ↔ Sandbox bidirectional sync.**~~ Landed in `796fa86`.
- **Q6 / Q11 — AdapterVolumeFileStore (mode 1).** Only mode 2 ships.

## 2a. Deferred sub-questions

- Snapshot trigger policy beyond kill-time.
- Big-file / binary handling (gitignore exclude list, per-file size
  cap, blob-store overflow).
- HTTP/2 for SSE (uvicorn `--http=h2`; matters at ≥7 tabs).
- Richer SSE event schema — `RunCancelled`, `ToolCallParseError`,
  `MaxTurnsExceeded`, `SandboxKilledIdle` as distinct event types.
- `GET /workspaces/{id}/events?since=<msg_id>` reconnection endpoint.

---

## 3. Open backend work — design + ordering

### 3.1  WorkspaceRegistry — sticky per-workspace state  *(prerequisite for §3.2 + §3.3 + §3.4)*

**Why first:** interrupt, idle-kill, and FS sync all need a single
source of truth per workspace: which sandbox handle is alive, which
agent turn is in flight, when was last activity. Today that state is
scattered (handle is per-request inside `AgentToolContext`; no task
registry).

**Shape**

```python
# src/workspace_app/api/registry.py
@dataclass
class WorkspaceSession:
    workspace_id: str
    handle: SandboxHandle | None = None        # lazy
    current_turn: asyncio.Task | None = None   # in-flight agent run
    last_active: datetime = field(default_factory=_now)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

class WorkspaceRegistry:
    def __init__(self, sandbox: Sandbox, default_spec: SandboxSpec): ...
    async def session(self, workspace_id: str) -> WorkspaceSession: ...
    async def ensure_handle(self, ws: WorkspaceSession) -> SandboxHandle: ...
    async def kill_idle(self, threshold: timedelta) -> list[str]: ...
    async def close_all(self) -> None: ...
```

**Tests:** session created on first lookup; same workspace returns same
session; `ensure_handle` calls `sandbox.create` exactly once across
concurrent callers (lock); `kill_idle` only kills past-threshold;
`close_all` kills every alive handle.

---

### 3.2  Interrupt on new message *(Q10 / c3)*

**Approach:** POST handler acquires `session.lock`, cancels any existing
`session.current_turn`, awaits unwind, ensures a `RunCancelled` event
is delivered to the cancelled stream's subscriber, starts new turn.

`LitellmAgentRunner.run` becomes cancellation-aware: catch
`asyncio.CancelledError`, attempt to abort the underlying `Runner`
streaming context, yield `RunCancelled`, re-raise.

**Files:**
- `api/registry.py` — turn registry on §3.1
- `api/app.py` — POST handler grows cancel→start dance
- `api/runner.py` — Protocol unchanged; impl handles cancellation
- `api/events.py` — add `RunCancelled` variant

**Tests:**
- Two concurrent POSTs same workspace: first stream sees `RunCancelled`
  then closes; second completes normally.
- Concurrent POSTs to *different* workspaces don't interfere.
- POST with no in-flight turn skips the cancel path.

**Frontend dependency:** `RunCancelled` event must appear in
`api/events.py` and the table in §4 below; FE agent will mirror it in
`web/src/events.ts` and add a renderer (FE §F4).

---

### 3.3  Idle timeout 15 min *(Q10 / b1)*

**Approach:** Background asyncio task started in `create_app`'s
`lifespan`. Wakes every 60s, calls
`registry.kill_idle(threshold=15min)`. Threshold configurable per
`AgentConfig.idle_timeout_seconds` (field already exists, default 900).

Each completed turn bumps `last_active`; lifespan kills survivors on
shutdown via `close_all`.

**Files:**
- `api/registry.py` — `kill_idle`
- `api/app.py` — lifespan wires background task; `idle_timeout` kwarg
  on `create_app`

**Tests:** threshold 0.2s → handle gone; active session within window
stays; `close_all` on shutdown kills survivors.

**Footgun:** background `pip install` left in sandbox dies on
idle-kill. Document; out of scope for v1.

**Frontend dependency:** emit `SandboxKilledIdle` event into any open
stream subscribed to that workspace (so the UI can show "sandbox went
to sleep; next command will cold-start"). FE §F5 mirrors and renders.

---

### 3.4  FS ↔ Sandbox bidirectional sync *(Q11)*

The single highest-impact gap. Three operations:

1. **On `ensure_handle` (post-create) → full restore.** Iterate every
   path in `FileStore.ls(ws)`, `sandbox.upload(handle, data, path)`.
   After restore, take a baseline manifest (path → sha256).

2. **Before each `exec` tool call → flush dirty.** `FileStore` tracks
   "paths written since last sync" per workspace. On flush: upload
   each, clear the dirty set, update baseline.

3. **Before `kill` (and idle kill from §3.3) → reverse sync.** Walk
   sandbox `/workspace`, sha256 each file, diff against baseline. For
   each changed/new path NOT in ignore list, `sandbox.download` →
   `FileStore.write`. **Deleted-in-sandbox paths are not propagated
   for v1** (safer default).

**Default ignore list** (configurable per workspace via `AgentConfig`):
`.venv/`, `node_modules/`, `__pycache__/`, `.git/objects/`, `*.pyc`,
`*.pyo`, `.pytest_cache/`, `.ruff_cache/`, files >10 MB.

**Walk-the-FS API:** Sandbox Protocol gains `walk(handle, root) ->
list[(path, size, mtime)]`. Implement for Mock/Local/Docker.

**Where:** new `src/workspace_app/sync/` package. `SandboxSync(filestore,
sandbox)` with `restore`/`flush`/`reverse`. Driven from
`WorkspaceRegistry` on create/idle/kill, and from `exec_impl` in
`agent/tools.py` (flush-before-exec).

**Files:**
- `sandbox/protocol.py` — add `walk` (Mock + Local + Docker impls)
- `filestore/protocol.py` — `dirty_paths(ws)`, `clear_dirty(ws)`
- `filestore/specstar_impl.py` — dirty tracking
- `sync/` (new) — `SandboxSync`, ignore-list, hash helpers
- `agent/tools.py` — `exec_impl` calls `ctx.sync.flush(...)` first
- `api/registry.py` — calls `sync.restore` after create, `sync.reverse`
  before kill

**Tests:**
- `write_file` tool then `exec(["cat", path])` sees the content.
- Sandbox `exec(["sh", "-c", "echo z > /workspace/x"])` → kill →
  FileStore now has `/x` with `z`.
- Reverse-sync skips ignored paths and >10 MB files.
- Restore is idempotent.
- Concurrent flushes serialized by session lock.

The snapshot-trigger sub-question (§2a) falls out: reverse-sync runs
on idle-kill. Adding `POST /workspaces/{id}/snapshot` later is trivial.

---

### 3.5  AdapterVolumeFileStore (mode 1) *(Q6 / Q11)*

For workspaces too large to round-trip through specstar.

**Approach:** files live at `root_dir/{workspace_id}/...` on host.
`SandboxSpec` gains `volume_mounts: dict[str, str]`;
`AdapterVolumeFileStore.host_path(workspace_id) -> Path` lets the
registry bind-mount at `/workspace`. In mode 1 the sandbox IS the
FileStore; `SandboxSync.flush`/`reverse_sync` become no-ops.

Per-mode policy from grill-me Q11:

| Mode                       | Lifecycle | Sync ops                       |
|----------------------------|-----------|--------------------------------|
| 2 (SpecstarFileStore)      | a2+       | restore/flush/reverse all real |
| 1 (AdapterVolumeFileStore) | a3        | all no-op                      |

**Files:**
- `filestore/adapter_volume.py` (new)
- `sandbox/protocol.py` — `SandboxSpec.volume_mounts` field
- `sandbox/docker.py` + `local_process.py` — wire mounts
- `sync/` — `NoOpSandboxSync` for mode 1

**Tests:** write via FileStore → read via Sandbox `cat`; both see same
bytes without explicit sync. Killing sandbox preserves volume.

---

### 3.6  Refined SSE event schema  *(coordinated with FE §F4 + §F5)*

After §3.2 + §3.3 + §3.4 add new failure modes, generic `RunError` is
under-informative. Split:

- `RunCancelled` — user interrupted (§3.2)
- `ToolCallParseError(call_id, raw, hint)` — model produced
  un-parseable args
- `MaxTurnsExceeded(turns)` — agent didn't converge
- `SandboxKilledIdle` — sandbox is gone, next exec cold-starts
- `RunError` — catch-all (kept)

**Files:** `api/events.py` + emit sites (`api/app.py`,
`api/litellm_runner.py`). Update §4 below in the same commit.

---

### 3.7  Reconnect endpoint *(coordinated with FE §F6)*

`GET /workspaces/{id}/events?since=<msg_id>` — replay tail of the
conversation when SSE stream drops mid-run. Reads from Conversation in
specstar (append-only).

**Files:** `api/app.py`. Tests with `TestClient` + simulated disconnect.

(HTTP/2 — out of scope; document in §2a only.)

---

### 3.8  Files API for FE file browser *(NEW — driven by FE §F3)*

Frontend needs to list and read workspace files. Add:

- `GET /workspaces/{id}/files?prefix=<p>` → `[{path, size}]`. Reads via
  `FileStore.ls` + a size lookup (add `FileStore.stat(ws, path) ->
  FileStat` to the Protocol).
- `GET /workspaces/{id}/files/{path:path}` → file bytes (or text if
  small/decodable). Reads via `FileStore.read`. Returns 404 on
  `FileNotFound`.
- `PUT /workspaces/{id}/files/{path:path}` (optional, behind a flag) —
  let the UI edit files. Writes via `FileStore.write`.

**Files:**
- `filestore/protocol.py` — `stat(ws, path)`
- `filestore/specstar_impl.py` — impl
- `api/app.py` — three new routes

**Tests:** list reflects writes; read missing → 404; read returns
exact bytes; concurrent writes don't corrupt.

---

## 4. Cross-cutting contracts (FE/BE sync surface)

Anytime BE changes one of these, the FE agent must update
`web/src/events.ts` (for events) or its `fetch` paths (for routes).
**Update this table in the same commit** that changes the wire.

### SSE events (`api/events.py` ↔ `web/src/events.ts`)

| Variant | Status | Notes |
|---|---|---|
| `MessageDelta { text }` | ✅ shipped | |
| `ToolStart { call_id, name, args }` | ✅ shipped | |
| `ToolEnd { call_id, output }` | ✅ shipped | |
| `RunDone` | ✅ shipped | terminal |
| `RunError { message }` | ✅ shipped | becomes catch-all once §3.6 lands |
| `RunCancelled` | ✅ shipped | terminal — emitted on DELETE or second POST |
| `ToolCallParseError { hint, call_id?, raw? }` | ✅ shipped | non-terminal; retry follows with the hint as feedback to the model |
| `MaxTurnsExceeded { turns }` | ✅ shipped | terminal — agent burned its turn budget |
| `SandboxKilledIdle` | ⏸ deferred | needs registry refactor (keep session entry after kill) — defer until UX requests it |

### HTTP routes consumed by SPA

| Route | Purpose | Status |
|---|---|---|
| `POST /workspaces/{id}/messages` | start an agent turn, returns SSE | ✅ |
| `GET /workspace` (specstar auto) | list workspaces | ✅ |
| `POST /workspace` (specstar auto) | create workspace | ✅ |
| `GET /conversation` (specstar auto) | list conversations | ✅ |
| `GET /conversation/{id}` (specstar auto) | get one conversation | ✅ |
| `GET /workspaces/{id}/files` | list files in workspace | ✅ shipped |
| `GET /workspaces/{id}/files/{path:path}` | read file | ✅ shipped |
| `DELETE /workspaces/{id}/messages/current` | cancel in-flight turn | ✅ shipped |
| `GET /workspaces/{id}/events?since=<id>` | reconnect / catch up | ⏳ §3.7 |

---

## 5. Principles

- **Protocol-first.** Adding a method to `Sandbox`/`FileStore`/
  `AgentRunner` means updating *all* impls (Mock + Local + Docker;
  Specstar + future Volume) and writing tests for each. Don't ship a
  half-implemented abstraction.
- **Bias to in-process state for v1.** WorkspaceRegistry, dirty-path
  trackers, idle timers — all in-memory.
- **Tests first via `/tdd`.** Red→green vertical-slice discipline.
- **Honesty over scope creep.** Split + update this doc rather than
  letting items sprawl.

---

## 6. Order

1. §3.1 WorkspaceRegistry — opens 3.2 + 3.3 + 3.4.
2. §3.4 FS↔Sandbox sync — biggest correctness gap; also makes 3.3
   safe (idle kill won't lose in-flight work).
3. §3.2 Interrupt — cheap on top of 3.1.
4. §3.3 Idle kill — cheap on top of 3.1 + 3.4.
5. §3.6 SSE event refinement — small, motivated by 3.2/3.3/3.4.
6. §3.8 Files API — unblocks FE §F3 (file browser); can land in
   parallel with §3.2-§3.6, no dependency on registry.
7. §3.7 Reconnect endpoint.
8. §3.5 AdapterVolumeFileStore — only when a real workspace size
   demands it.
