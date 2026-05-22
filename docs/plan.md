# workspace-app — Plan

Living document. Tracks what's done, what's left, and *how* each open item
should be tackled. Order in §3 is priority high → low.

---

## 1. Done

Implementation scaffolding from the eight `/grill-me` steps, plus the
small-model retry path. Commits in chronological order:

| # | Commit  | Scope |
|---|---------|-------|
| 1 | `05e2186` | Bootstrap + Sandbox Protocol + MockSandbox |
| 2 | `9ae9921` | FileStore Protocol + SpecstarFileStore (mode 2) |
| 3 | `74a3960` | Workspace / AgentConfig / Conversation specstar Structs |
| 4 | `086e2e1` | Agent tool wrappers (T2: exec + read/write/ls/exists/delete) |
| 5 | `d06e3fb` | FastAPI app factory + SSE message endpoint |
| 6 | `64518d9` | LocalProcessSandbox + DockerSandbox adapters |
| 7 | `8c8520e` | LitellmAgentRunner (OpenAI Agents SDK + LiteLLM/Ollama) |
| 8 | `b4f6db0` | React SPA chat page + entrypoint + CLAUDE.md docs |
| + | `261ae64` | LitellmAgentRunner retry-with-feedback for small-model errors |

Test surface: 94 passed / 1 live-skipped on a cold box, 100% coverage
(statement + branch). All `ruff check`/`ruff format`/`ty check` clean.

---

## 2. Not done — locked-in `/grill-me` decisions not yet implemented

These were architectural commitments in §Q6/Q10/Q11 of the grill-me
session, not "nice to have". The MVP is not honest until they're in.

- **Q10 / c3 — Interrupt on new user message.** Currently a second POST
  while a turn is in flight races against the first. No cancellation.
- **Q10 / b1 — Idle kill after 15 min.** Sandboxes lazy-created on first
  `exec` live until the process exits. No timer.
- **Q11 — FS ↔ Sandbox bidirectional sync.** `write_file` lands in
  specstar but never reaches the sandbox; `exec`'s shell writes never
  reach specstar. The "workspace has a persistent filesystem" promise is
  currently a lie.
- **Q6 / Q11 — AdapterVolumeFileStore (mode 1).** Only mode 2 ships; the
  Q6 contract was "support both, default to 2."

## 2a. Deferred sub-questions from grill-me

Real but lower priority than §2 — most won't bite until production load
or a specific tool/workflow needs them.

- Snapshot trigger policy beyond kill-time (idle, explicit "save", every-N
  turns).
- Big-file / binary handling: gitignore-style exclude list, per-file size
  cap, blob-store overflow for >N MB files.
- SSE `Last-Event-ID` reconnection — replay the tail of the conversation
  on reconnect.
- HTTP/2 for SSE — uvicorn supports it; only matters once a user opens
  ≥7 tabs (HTTP/1.1 per-origin connection cap).
- Richer SSE event schema: `RunCancelled`, `ToolCallParseError`,
  `MaxTurnsExceeded` as distinct event types instead of a generic
  `RunError`.

---

## 3. Open work — design + ordering

### 3.1  WorkspaceRegistry — sticky per-workspace state  *(prerequisite for §3.2 + §3.3)*

**Why first:** Interrupt and idle-kill both need a single source of truth
per workspace: which sandbox handle is alive, which agent turn is in
flight, when was the last activity. Today that state is scattered
(handle is per-request inside `AgentToolContext`; no task registry).

**Shape**

```python
# src/workspace_app/api/registry.py
@dataclass
class WorkspaceSession:
    workspace_id: str
    handle: SandboxHandle | None = None        # lazy, may be None
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

**Test ideas:** session is created on first lookup; same workspace returns
same session; `ensure_handle` calls `sandbox.create` exactly once across
concurrent callers (lock); `kill_idle` only kills sessions past threshold;
`close_all` kills every alive handle.

---

### 3.2  Interrupt on new message *(Q10 / c3)*

**Approach:** POST handler acquires `session.lock`, cancels any existing
`session.current_turn`, awaits its unwind, emits a `RunCancelled` event
into the cancelled stream (if subscribers exist), then starts the new
turn task.

`LitellmAgentRunner.run` becomes cancellation-aware: it must catch
`asyncio.CancelledError`, attempt to abort the underlying `Runner`
streaming context, and yield `RunCancelled` before re-raising (or
swallowing) the cancellation.

**Files:**
- `api/registry.py` (new) — turn registry on top of §3.1
- `api/app.py` — POST handler grows cancel→start dance
- `api/runner.py` — Protocol gains nothing new; impl handles cancellation
- `api/events.py` — add `RunCancelled` variant
- `web/src/events.ts` — mirror

**Test ideas:**
- Two concurrent POSTs to same workspace: first stream sees
  `RunCancelled` then closes; second stream completes normally.
- Concurrent POSTs to *different* workspaces don't interfere.
- POST that arrives while no turn is in flight skips the cancel path.

---

### 3.3  Idle timeout 15 min *(Q10 / b1)*

**Approach:** Background asyncio task started in `create_app`'s
`lifespan` hook. Wakes every 60s, calls
`registry.kill_idle(threshold=15min)`. Threshold is configurable via
`SandboxLifecyclePolicy` (so tests can use 200 ms).

Touching the session resets `last_active`: POST handler bumps it after
the run completes (or on each event, undecided — start with after-run
which is simplest).

**Files:**
- `api/registry.py` — `kill_idle`
- `api/app.py` — lifespan wires background task; new `idle_timeout`
  kwarg on `create_app`
- `resources/agent_config.py` — `idle_timeout_seconds` field already
  exists (default 900); read it here

**Test ideas:**
- Threshold 0.2s: create session, wait, observe handle gone.
- Active session within window stays alive.
- `close_all` on shutdown kills survivors.

**Footgun:** background `pip install` left running inside the sandbox
dies when idle-kill nukes it. Out of scope for v1 — document and move on.

---

### 3.4  FS ↔ Sandbox bidirectional sync *(Q11)*

The single highest-impact gap. Three operations:

1. **On `ensure_handle` (post-create) → full restore.** Iterate every
   path in `FileStore.ls(ws)`, `sandbox.upload(handle, data, path)` for
   each. After restore, take a baseline manifest (path → sha256) so we
   can detect changes later.

2. **Before each `exec` tool call → flush dirty.** `FileStore` tracks
   "paths written since last sync" per workspace (a `set[str]` in-memory
   alongside the existing cache). On flush: for each dirty path,
   `sandbox.upload`; clear the dirty set; update the baseline manifest.

3. **Before `kill` (and on idle kill from §3.3) → reverse sync.** Walk
   the sandbox's `/workspace`, compute sha256 of each file, compare to
   baseline. For each changed/new path NOT in the ignore list, read it
   out via `sandbox.download` and write to `FileStore`. Deleted-in-
   sandbox paths are *not* propagated for v1 (avoids accidental
   destruction; revisit if real workflows demand it).

**Default ignore list** (configurable on `AgentConfig`):
`.venv/`, `node_modules/`, `__pycache__/`, `.git/objects/`, `*.pyc`,
`*.pyo`, `.pytest_cache/`, `.ruff_cache/`, files >10 MB.

**Walk-the-FS API:** Sandbox needs a `walk(handle, root)` op that
returns `list[(path, size, mtime)]` so we can avoid downloading
everything blindly. Add to Protocol; implement for Mock/Local/Docker.

**Where it lives:** `src/workspace_app/sync/` — a new package, *not*
shoved into FileStore. `SandboxSync(filestore, sandbox)` with
`restore(ws, handle)`, `flush(ws, handle)`, `reverse(ws, handle,
ignore=...)`. Driven from `WorkspaceRegistry` on the transitions
above, and from `exec_impl` in `agent/tools.py` (flush-before-exec).

**Files:**
- `sandbox/protocol.py` — add `walk` (new abstract method, 3 impls)
- `filestore/protocol.py` — add `dirty_paths(ws) -> set[str]` and
  `clear_dirty(ws)` for the in-memory tracker
- `filestore/specstar_impl.py` — implement dirty tracking
- `sync/` (new) — `SandboxSync`, ignore-list utilities, hash helpers
- `agent/tools.py` — `exec_impl` calls `ctx.sync.flush(...)` first
- `api/registry.py` — calls `sync.restore` after create,
  `sync.reverse` before kill

**Test ideas:**
- `write_file` tool → `exec(["cat", path])` sees the content (round
  trips through SpecstarFileStore → flush → sandbox).
- Sandbox `exec(["sh", "-c", "echo z > /workspace/x"])` → kill →
  FileStore now has `/x` with content `z`.
- Reverse-sync skips ignored paths (`node_modules/`, files >10 MB).
- Restore is idempotent (running twice gives same state).
- Concurrent flushes: one in-flight, second waits (lock on session).

**The Q6 sub-question — snapshot triggers besides kill-time** — falls
out naturally: reverse-sync runs on idle-kill (§3.3) which is the v1
"snapshot moment." Adding explicit `POST /workspaces/{id}/snapshot` is
trivial later.

---

### 3.5  AdapterVolumeFileStore (mode 1) *(Q6 / Q11)*

For workspaces too large to round-trip through specstar.

**Approach:** files live on host disk at `root_dir/{workspace_id}/...`.
Sandbox `create` takes a `volume_mounts` field on `SandboxSpec`;
`AdapterVolumeFileStore` exposes `host_path(workspace_id) -> Path` so
the registry can bind-mount it into the sandbox at `/workspace`.

In mode 1 there's effectively **no sync** — the sandbox IS the
FileStore (via bind mount). `SandboxSync.flush` and `reverse_sync` are
no-ops. `restore` is also a no-op since the volume persists.

This is also where the Q11 **a3 lifecycle** (sandbox alive whenever
workspace is open in UI) lives, because without sync, the only way to
read files is to have the sandbox up. Per-mode policy:

| Mode                       | Lifecycle | Sync ops        |
|----------------------------|-----------|-----------------|
| 2 (SpecstarFileStore)      | a2+       | restore/flush/reverse all real |
| 1 (AdapterVolumeFileStore) | a3        | all no-op       |

**Files:**
- `filestore/adapter_volume.py` (new)
- `sandbox/protocol.py` — `SandboxSpec.volume_mounts: dict[str, str]`
  field
- `sandbox/docker.py` / `local_process.py` — wire mounts
- `sync/__init__.py` — `NoOpSandboxSync` for mode 1

**Test ideas:**
- Write via FileStore, read via Sandbox `exec(["cat"])`, both see same
  bytes without explicit sync.
- Killing the sandbox doesn't drop the volume — re-create reads stale
  content.

---

### 3.6  Refined SSE event schema

Once §3.2 and §3.4 add new failure modes, the generic `RunError` is
under-informative. Split:

- `RunCancelled` — user interrupted (§3.2)
- `ToolCallParseError(call_id, raw, hint)` — model produced
  un-parseable args
- `MaxTurnsExceeded(turns)` — agent didn't converge
- `SandboxKilledIdle` — your sandbox is gone, next exec will cold-start
- existing `RunError` becomes the catch-all

**Files:** `api/events.py` + `web/src/events.ts` + Chat.tsx switch
arms. Tests already follow a parametrized pattern, easy extension.

---

### 3.7  Reconnect + HTTP/2 *(lowest priority)*

- `GET /workspaces/{id}/events?since=<msg_id>` — fall back when SSE
  POST stream drops mid-run. Reads from the Conversation in specstar
  (it's append-only). Frontend: on `EventSource` error, hit this
  endpoint with the last seen ID.
- HTTP/2 — `uvicorn --http=h2 --ssl-certfile=...`. Only matters when a
  user opens ≥7 tabs against the same origin. Documenting > coding.

---

## 4. Principles for any of the above

- **Protocol-first.** Adding a method to `Sandbox`/`FileStore`/`AgentRunner`
  means updating *all* impls (Mock + Local + Docker; Specstar + future
  Volume) and writing tests for each. Don't ship a half-implemented
  abstraction.
- **Bias to in-process state for v1.** WorkspaceRegistry, dirty-path
  trackers, idle timers — all in-memory. Distributing comes when the
  app actually runs multi-replica.
- **Tests first via `/tdd`.** Same red→green vertical-slice discipline
  used for steps 1-8. No "write all the code, write tests last" runs.
- **Honesty over scope creep.** If something on this list turns out
  bigger than a vertical slice, split it and update this doc rather
  than letting it sprawl.

---

## 5. Suggested order

1. §3.1 WorkspaceRegistry — opens the door for 3.2 + 3.3.
2. §3.4 FS↔Sandbox sync — biggest correctness gap. Also makes §3.3's
   idle-kill safe (no in-memory work lost).
3. §3.2 Interrupt — cheap on top of 3.1.
4. §3.3 Idle kill — cheap on top of 3.1 + 3.4.
5. §3.6 SSE event schema refinement — small, motivated by 3.2/3.3/3.4.
6. §3.5 AdapterVolumeFileStore — only if a real workspace size demands
   it.
7. §3.7 Reconnect + HTTP/2 — only when someone complains.
