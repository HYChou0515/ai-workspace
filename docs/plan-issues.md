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

**Install model — prebuilt relocatable venv (chosen):** a tool is prebuilt once
on the host as a `uv venv --relocatable` + the installed CLI
(`scripts/prebuild_tools.py`), then at provision time the whole package is
`tar`→`upload`→extracted into the sandbox (`--no-same-owner`, since we're
mapped-root in the userns jail). The sandbox needs **no uv / network / build
step**; `invoke` runs the copied venv binary. Verified end-to-end: extract works
in the real chroot jail; provision+invoke chain works via `ensure_sandbox`.

**Wiring (no launcher):** `ToolDef`s stay deploy-level code
(`rca/sample_tools.py`), passed to `create_app(tool_defs=...)` by `__main__`
(only those whose prebuilt package exists are advertised). A **template profile
binds its own `AgentConfig`** via `_config.json` (`load_template_config`;
resolution = attached → template config → store default), so selecting the
`tool-demo` template — not a launcher — lists the tools in `allowed_tools` and
turns them on.

**Base-python caveat (important):** a relocatable venv still resolves its base
python *by path*, so build the venv against the python the SANDBOX has:
- production LocalProcessSandbox in a py3.12 pod → `prebuild_tools.py --python
  3.12` (the pod's `/usr/bin/python` is overlaid into the jail), isolate on;
- the dev box here has only system python 3.9 (tools need ≥3.10) and uv's python
  isn't in the jail → run with `SANDBOX_ISOLATE=false` (host python reachable).
Bundling a portable python *into* the package was tried and rejected: the
standalone `libpython` fails to resolve inside the chroot (linker symbol error).

**Local test flow:** `uv run python scripts/prebuild_tools.py` then
`SANDBOX_ISOLATE=false uv run python -m workspace_app` → new investigation →
`tool-demo` template.

**Follow-ups:** provisioning cache (re-copying a big venv per cold sandbox is
slow), private-repo secrets, and confirming the production base-python /
sandbox-image choice.

---

## Parked

- **#16 multi-pod in-memory state.** Concrete bug: `SpecstarFileStore._ids`
  in-memory cache → not actually cross-pod (fix: derive resource id from
  `workspace_id`, no cache). Sandbox handles + in-flight turn sessions are
  inherently pod-local (sticky routing / externalize — deployment, not a code
  tweak). Today the deploy is single-replica (`replicas: 1`, *"does not
  horizontally scale as-is"*), so this is forward-prep — revisit when scaling.
- **#22 FE half.** Show the persisted metrics (and confirm reasoning/tool cards)
  on reload in `AgentEntryView` / the chat panels.
