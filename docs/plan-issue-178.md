# Plan — Issue #178: per-step workflow status (alive vs dead)

> "workflow each step 的狀態需要能夠看到更詳細的內容, 不然我不知道他是死了還是正在跑"

Builds on #100 (workflows) and the prior workflow-observability work. Grill-locked
(see "Locked decisions" below). Flat integer phases per project convention; backend
via `/tdd` + 100% coverage gate, frontend via `/tdd` + vitest. No new auto-kill —
display only, consistent with the earlier rejection of timeout/watchdog/heartbeat.

## Problem

Each step emits only point events (`step_started` → silence → `step_passed/failed/
skipped/retrying`), carrying just `phase/name/key` (+`reason` on fail/retry). No
timing, no in-flight progress, no retry count. The prominent workflow UI
(`WorkflowPhaseDiagram` / `WorkflowRunPanel`) is **phase-level only**; per-step
status is scattered in the chat feed. So:

- A long **deterministic** `sandbox_node` (e.g. a 5-min script) is silent until it
  ends → looks dead. (Agent steps already stream reasoning/tools into the chat.)
- There is **no per-step status board** to watch "which step, how long, how many
  retries", and it does not survive a reload (per-step state is live-SSE only).

## Locked decisions (from grill)

1. **Backend real liveness, not a FE-only timer.** A FE timer keeps ticking when the
   backend is wedged, so it proves only "FE still streaming". We stream real output
   where it exists.
2. **Home = a per-step status board in the workflow panel.** Each phase in the
   diagram expands to its steps; the chat feed stays the home for agent reasoning.
3. **Stream deterministic stdout** (`sandbox_node`) via the existing `sandbox.exec
   on_output` primitive → a new `step_output` event folded into the running step
   row. **Ingest stays as-is** (per-file step rows already move; no `Ingestor`
   surgery — that within-one-file progress is #162 territory).
4. **Per-step detail split:** deterministic rows expand to live stdout; agent rows
   show status+elapsed and point to the chat. No journal-artifact file inspector.
5. **Persist per-step state on `WorkflowRun`** (not ephemeral SSE) so the board
   survives a reload and shows server-side elapsed ("running 4:32" even when wedged).
   Consistent with "WorkflowRun holds status, not results" — stdout stays ephemeral.
6. **Collapse loop elements.** Same-named loop steps fold into the phase `done/total`
   counter; the board persists/shows only distinct-named steps (with duration) + the
   currently-running element + failed/retrying elements (reuse the existing
   `failures` list). Keeps the resource bounded and the board uncluttered.
7. **Silent steps (no stdout at all):** the only honest signals are server-side
   elapsed + **making the stream connection status visible** (connected = backend
   reachable; dropped = "may have stopped"). **No heartbeat** — a loop-alive ping
   can't detect a wedged subprocess (it would falsely read "alive"), and total
   backend death already shows as an SSE disconnect. We document the ceiling: a
   silent step's "wedged vs slow" is undecidable without output — which is exactly
   why we stream stdout where it exists.

## Phases

### P1 — `step_output` event (schema)
- `workflow/events.py`: add `StepOutput(phase, name, text, key="")` frozen dataclass;
  add to `WorkflowEvent` union.
- `api/events.py`: fold into the `AgentEvent` union + `to_sse` (frozen dataclass with
  a `type` field — no extra wiring).
- `web/src/events.ts`: mirror `StepOutput` + add to the union.
- Tests: to_sse round-trip; FE type guard.

### P2 — stream sandbox stdout
- `workflow/handle.py`: widen `RunSandbox` to accept an optional `on_output`
  (`OutputSink = Callable[[bytes], None]`, matching `sandbox/protocol.py`).
- `workflow/steps.py` `sandbox_node`: pass an `on_output` sink that emits
  `StepOutput(phase, name=name or phase, key, text=chunk.decode(...))` via the engine
  `_emit(wf, …)`.
- `api/app.py` `_wf_run_sandbox`: forward `on_output` to
  `sandbox.exec(handle, cmd, on_output=…)`.
- `workflow/orchestrator.py` `_on_event`: **short-circuit `StepOutput`** — publish on
  the stream and return; do NOT enter `_apply_progress` (no `_patch` per chunk).
- Tests: `MockSandbox` streaming chunks → `sandbox_node` emits `StepOutput` per chunk;
  orchestrator publishes `StepOutput` without patching the resource.

### P3 — persist per-step state on `WorkflowRun`
- `workflow/run.py`: add `StepState(phase, name, key="", status="running", attempts=1,
  reason="", started: int|None, ended: int|None)` + `steps: list[StepState]` (additive,
  default empty → no migration; not indexed — display data).
- `workflow/orchestrator.py` `_apply_progress`: upsert/collapse the step record
  alongside the existing phase-counter `_patch` (same write, no extra DB round-trips):
  - `StepStarted` → upsert running record, stamp `started` (epoch ms).
  - `StepRetrying` → `attempts += 1`, set `reason`.
  - `StepPassed`/`StepSkipped` → if `key == ""` keep as `passed/skipped` + `ended`;
    if `key != ""` (loop element) drop the record (folds into `phase.done`).
  - `StepFailed` → keep as `failed` + `ended` + `reason` (failed loop elements also
    remain in the existing `failures` list).
- Time source: a small injectable `now_ms` (mirror how `driver` stamps `started`/
  `ended`) so tests are deterministic.
- Tests: a 3-element loop leaves 0 step rows + `done=3`; a distinct named step keeps
  its `passed` row with a duration; a failed element keeps a `failed` row; reload
  (re-`_get`) returns the persisted `steps`.

### P4 — step status board (FE)
- New board (extend `WorkflowPhaseDiagram` or a child `WorkflowStepBoard.tsx`): each
  phase row expands to its steps from `run.steps` (poll), overlaid with live SSE:
  status badge, elapsed (server `started` → ticking now / final duration on `ended`),
  retry count, `reason`. Deterministic rows expand to live stdout (fold `StepOutput`
  into the running row's `liveOutput`, mirroring the `tool_log` handling). Agent rows
  show status + elapsed + a "see conversation" hint.
- Collapsed loop view: "{done}/{total} · 1 failed · running {key} ({elapsed})" from the
  phase counter + the running step record + `failures`.
- Move step rendering **out of the chat feed** (`agentLog.ts` stops folding the
  `step_*` events into feed lines) so steps have one home; the chat keeps agent turns
  (+ optionally a light `phase_entered` divider).
- Tests (vitest): board renders pending/running/passed/failed/skipped/retrying;
  elapsed ticks for a running row and freezes on done; deterministic row shows live
  stdout; loop collapses to a counter; agentLog no longer emits step feed lines.

### P5 — connection status visible (FE)
- Surface the SSE stream connection state in the run panel ("Connected — backend
  reachable" / "Disconnected — the run may have stopped"), reusing the existing stream
  state from `useWorkflow`/`useAgent`. This is the silent-step liveness backstop.
- Tests (vitest): connected vs disconnected rendering.

### P6 — copy + i18n + wiring
- Strings via `useT` (zh-TW + en); de-jargon per the UI-copy rule (no `sandbox`/`stdout`
  internals in user-facing text — "execution environment" / "output"). Mount the board
  in `WorkflowRunPanel`.
- Tests (vitest): locale strings resolve; panel mounts the board.

### P7 — DoD: full gate + live check
- Backend: `coverage … --fail-under=100`, `ruff check`, `ruff format --check`,
  `ty check`. Frontend: vitest + `pnpm typecheck` + `pnpm build`.
- **Live check** (LLM/workflow features need a real run, not just fake-LLM tests):
  run a real workflow against local Ollama and confirm, by eye:
  1. the board moves step-by-step (a deterministic phase no longer looks frozen);
  2. a long `sandbox_node`'s stdout streams live into its row;
  3. reloading mid-run keeps the board + a correct server-side elapsed;
  4. killing the stream shows the "may have stopped" banner.

## Deferred (out of scope, documented)
- Within-one-file ingest progress (Ingestor is opaque; #162's index-status territory).
- A journal-artifact file inspector (click a step → its `step_*/<key>.json`).
- Full stdout replay after a reload for a still-running step (ephemeral; reappears as
  new chunks stream — the persisted row + elapsed is the reload signal).
- Any new auto-kill / timeout / watchdog / heartbeat (display only; user self-Stops).
