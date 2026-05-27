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
| 17 | agent has no cross-turn memory | ☐ todo |
| 21 | sandbox tool provisioning (wiring) | ☐ todo — mechanism done, not wired |
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

## #21 — sandbox tool provisioning (wiring)

**Mechanism: DONE** (`agent/provision.py` + `tests/agent/test_provision.py`) —
`ToolDef` (declarative `setup`/`invoke`/`params`), `provision_tools`,
`build_provisioned_tools`; proven end-to-end installing the two `sample-tools/`
into a real sandbox and chaining them. **Not yet wired into a real turn.**

**Open decisions (grill before wiring)**
1. Where `ToolDef`s live: a dedicated registry/resource vs `AgentConfig`.
2. Provision timing: eager on sandbox create (in `InvestigationRegistry` /
   `ensure_sandbox`) vs lazy on first invoke.
3. `_agent_for` appends `build_provisioned_tools(allowed)` to the agent's tools.
4. Caching (re-clone/re-sync per cold sandbox is slow), private-repo secrets.

**Approach (once decided)**
- A `ToolRegistry` of `ToolDef`s; `AgentConfig.allowed_tools` gates which apply.
- `ensure_sandbox` / registry runs `provision_tools` for allowed defs after
  create; `_agent_for` adds the provisioned `FunctionTool`s.
- Tests: a turn with an allowed provisioned tool installs it + the agent can
  call it (scripted runner / the real-sandbox integration test already proves
  the exec path).

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
