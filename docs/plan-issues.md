# Issue plan — backend batch

Living checklist for the open GitHub issues. Decisions below were settled in a
`/grill-me` pass; we implement them **one at a time**, each via `/tdd` with its
own commit. Tick a box when it lands.

**Order:** #20 → #15 → #22 (BE half) → #17 → #21 (provisioning wiring).
**Parked:** #16 (multi-pod), #22 (FE reload display).

| # | Title | Status |
|---|-------|--------|
| 20 | read_file blows the context window | ✅ done (1599ec0) |
| 15 | embed service timeout | ✅ done (1b8a538) |
| 22 | persist token metrics (BE half) | ✅ done (1057171) |
| 17 | agent has no cross-turn memory | ✅ done (91df4a0) |
| 21 | sandbox tool provisioning | ✅ done — prebuilt-venv copy + template→AgentConfig; caching/secrets/base-python follow-ups |
| 16 | multi-pod in-memory state | ⏸ parked |

---

## #20 — `read_file` caps + pagination

**Problem:** `read_file_impl` decodes and returns the whole file → a large file
blows the context window.

**Decision:** line-based `offset`/`limit` (Claude-Code style); caps live in
`Settings` (env-overridable), sized for a large-context model; over-cap →
truncate + a notice telling the agent to use `offset`/`limit`.

**Approach**
- `Settings`: `read_file_max_lines` (default ~2000), `read_file_max_chars`
  (total-response budget, default ~200_000 ≈ ~50k tok). Env: `READ_FILE_MAX_LINES`,
  `READ_FILE_MAX_CHARS`.
- `read_file_impl(path, offset=None, limit=None)`: slice lines `[offset, offset+limit)`
  (defaults: from start, up to `max_lines`); enforce `max_chars` on the returned
  slice; append a truncation notice (lines/bytes elided, how to read more).
- Files: `agent/tools.py` (signature + slicing), `factories.py` (Settings),
  wire the caps into the tool (via ctx or closure).
- Tests: returns whole small file; caps a long file + notice; `offset`/`limit`
  window; pathological long line capped by `max_chars`.

## #15 — embedder timeout / retries / batching

**Problem:** `LitellmEmbedder._embed` calls `litellm.embedding(model, input)` with
no timeout/retries, and a big doc sends every chunk in one request.

**Decision:** configurable timeout + retries + batch size in `Settings`; failure
→ doc `error` (already logged via #fix earlier).

**Approach**
- `Settings`: `kb_embed_timeout` (60.0s), `kb_embed_num_retries` (2),
  `kb_embed_batch_size` (64). Env: `KB_EMBED_TIMEOUT`, `KB_EMBED_NUM_RETRIES`,
  `KB_EMBED_BATCH_SIZE`. Thread into `get_embedder`.
- `LitellmEmbedder.__init__` takes `timeout`, `num_retries`, `batch_size`;
  `_embed` chunks `texts` into `batch_size` groups, calls
  `litellm.embedding(..., timeout=, num_retries=)` per batch, concatenates.
- Tests: batching splits a >batch_size input into N calls in order + preserves
  order (fake `_embed`/monkeypatched `litellm.embedding`); timeout/retries passed
  through. (The live call stays `# pragma: no cover`.)

## #22 — persist token metrics (backend half)

**Problem:** reasoning + tool calls + citations persist, but the live token
metrics line is ephemeral → lost on reload.

**Decision:** persist the **final** `AgentMetrics` on the assistant message.
(FE reload-display is the parked front-end half.)

**Approach**
- Resources: add a `metrics` value to `Message` + `KbMessage` (e.g. a small
  struct `{prompt_tokens, completion_tokens, elapsed_ms}` or 3 optional ints).
- `turns.py` `gen()`: capture the last `AgentMetrics` into the assistant
  `TurnMessage`; `TurnMessage` gains the metrics fields.
- Persist mappings (`_to_rca_message`, KB `persist` + `_message_dict`) carry it.
- Tests: a turn that emits metrics persists them on the assistant message;
  getChat / conversation round-trips them.

## #17 — agent cross-turn memory

**Problem:** `engine.stream(key, content, ctx)` passes only the new message to
`runner.run`; the agent sees no prior turns.

**Decision:** replay our persisted history as the SDK `input` **list** (single
source of truth = our messages). Replay **user + assistant text** (skip
tool/reasoning plumbing — durable workspace files + answer summaries carry
continuity). History window configurable in `Settings`.

**Approach**
- `Settings`: `history_max_messages` (or char budget) — generous default.
- `runner.run` accepts the prior messages (or an input-items list);
  `engine.stream(key, content, ctx, *, history=...)` threads it; RCA (`app.py`)
  + KB (`kb_chat_routes.py`) pass their persisted messages (minus the just-added
  user msg, which is `content`).
- Build SDK input items: `[{role, content}, …, {role:"user", content}]` from the
  windowed user/assistant messages.
- Tests: a 2nd turn's runner input includes the 1st turn's user+assistant text;
  windowing caps old turns; tool/reasoning messages excluded from replay.

## #21 — sandbox tool provisioning ✅ wired

**Mechanism** (`agent/provision.py`): `ToolDef` is declarative
(`invoke`/`params`/`positional` + either `setup` argv steps **or** a `prebuilt`
package). `provision_tools` runs on `ensure_sandbox` for the config's
`allowed_tools`; `build_provisioned_tools` / `_agent_for` expose each as a
`FunctionTool` so the agent calls it with structured params (not improvised
`exec`).

**Install model — prebuilt SELF-CONTAINED package, mounted read-only (chosen):**
a tool is prebuilt once on the host (`scripts/prebuild_tools.py`) as a
`uv venv --relocatable` + the installed CLI **+ a bundled copy of its python** +
a `launch` script. The sandbox makes the shared prebuilt dir available at
**`/.tools` (read-only)** — a bind-mount when jailed, a symlink when not —
**outside the workspace** and with **no per-sandbox copy**. The sandbox needs no
uv / network / build step and no python of its own. Verified end-to-end in the
default isolate=true jail: both tools run (exit 0), `/.tools` is read-only
(write → rc≠0), and `walk` (the synced/visible workspace) shows only the user's
files. (`ToolDef.prebuilt` still exists as a copy fallback for backends that
can't mount; the sample tools leave it unset.)

**Workspace boundary:** the user workspace is `/root` (the agent's cwd/`~`);
the sandbox root is the infra area (system overlays + `/.tools`). `walk` /
upload / download / reverse-sync are scoped to the workspace, so tools + caches
(`HOME`/`XDG_CACHE_HOME` → `/tmp`) never leak into the file tree or specstar.
Portable: the `~` boundary needs only `exec`/`upload` (any backend incl.
gVisor); the read-only mount is a per-backend safety layer (image-bake for
Docker/gVisor).

**Why a `launch` script (the hard part):** inside the userns jail the process is
**AT_SECURE**, so glibc's loader ignores `$ORIGIN`/RPATH/LD_LIBRARY_PATH and the
implicitly-started interpreter can't find the bundled `libpython`. `launch`
starts the bundled python through the **explicit dynamic loader** (not
AT_SECURE) with the venv's site-packages on `PYTHONPATH`. (Repointing venv
symlinks / bundling alone fails with `undefined symbol: Py_BytesMain`.)

**Wiring (no launcher):** `ToolDef`s stay deploy-level code
(`rca/sample_tools.py`), passed to `create_app(tool_defs=...)` by `__main__`
(only those whose prebuilt package exists are advertised). A **template profile
binds its own `AgentConfig`** via `_config.json` (`load_template_config`;
resolution = attached → template config → store default), so selecting the
`tool-demo` template — not a launcher — lists the tools in `allowed_tools` and
turns them on.

**Test flow:** `uv run python scripts/prebuild_tools.py` (one-time; builds the
self-contained packages, ~200 MB each) then `uv run python -m workspace_app` →
new investigation → `tool-demo` template. No env vars.

**Follow-ups:** the packages are big (each bundles its own python) and re-copied
per cold sandbox — share one python bundle / cache the copy; private-repo
secrets for real (non-sample) tools.

---

## #16 — multi-pod state ✅ (filestore fixed; runtime needs sticky routing)

Target: **multi-replica**, hundreds of users (NOT single-replica). Verified the
whole in-memory inventory; only ONE thing is a code-fixable cross-pod data bug:

- **`SpecstarFileStore._ids` (FIXED).** It cached workspace_id→resource_id in
  memory, so a fresh pod created a DUPLICATE `_WorkspaceFiles` and pods couldn't
  see each other's files. Fix: the resource id is **deterministic** —
  `quote(workspace_id)` — so any pod `get()`s the one shared record. No cache,
  no index, no duplicates. (Tested: a 2nd instance on the same store sees the
  1st's files.) Files were always in specstar (shared); only the cache was wrong
  — so this matters even with sticky routing (a pod restart/failover gives a
  fresh, empty cache).

**Inherently pod-local runtime → sticky routing (deployment, chosen path A):** a
live subprocess / Jupyter kernel / in-flight turn task / in-process lock
physically lives on one pod and can't move. So route a workspace's requests to a
consistent pod (session affinity keyed by investigation/workspace id). This is
multi-replica + horizontal (workspaces spread across N pods → hundreds of
users); it is NOT single-replica. The pod-local set (verified by reading each):
`LocalProcessSandbox._dirs`, `DockerSandbox._containers`, `KernelService._kernels`,
`ChatTurnEngine._sessions`, `InvestigationRegistry._sessions`, `WorkspaceFiles._locks`.
NOT bugs: `SandboxSync._versions` (re-mirror at worst), `MonitorProcessor._groups`
(a trace is wholly on one pod). Dev/test: MockSandbox, MemoryFileStore.

**Deployment (done, path A):** `kubernetes/base` now —
- `pvc.yaml`: `ReadWriteOnce` → **`ReadWriteMany`** (every replica mounts the
  shared specstar store; needs an RWX storageClass — set per cluster).
- `deployment.yaml`: **`replicas: 3` + `RollingUpdate`** (was 1 + Recreate).
- `ingress.yaml`: **per-investigation** consistent-hash affinity —
  `upstream-hash-by: $rca_ws_key`, where a `configuration-snippet` sets
  `$rca_ws_key` to the id captured from `/investigations/<id>` or
  `/kb/chats/<id>` (else `$host`). Per-investigation, NOT per-user (a shared
  investigation must not split across pods). Needs the controller's
  `allow-snippet-annotations=true`; validate on the cluster.

(specstar confirmed multi-writer-safe. Path B — externalize the sandbox runtime
into a stateless remote sandbox service — remains the bigger future option.)

## Parked

- **#22 FE half.** Show the persisted metrics (and confirm reasoning/tool cards)
  on reload in `AgentEntryView` / the chat panels.
