# Plan — #288 Conversational steering of workflow runs + incremental resume

The normative target is **`docs/workflows.md` §10 (Steer-and-resume)**. This is the
implementation plan: flat integer phases, built via `/tdd`.

## What this is

`#288` is the **steer-and-resume that `#100` deferred**. The incremental-resume
engine already exists (FS-as-journal input-hash skip, §9); `#283` already made a run a
first-class object bound to its own chat. `#288` adds the **"say it in words"** layer
on top of the §9 escape hatch ("stop → edit/delete `step_*` in the file UI → Run").

**Flow:** free-text in a run's chat → a read-only **steerer** LLM turn proposes a
`SteerPlan` (rewrite input files + invalidate steps) → human reviews a confirm card
with the blast radius → **approve / reject / re-instruct** → on approve a deterministic
step applies the edits + deletes the invalidated artifacts → the **same run resumes**
(re-spawn; completed steps skip → incremental).

## Locked decisions (grilled)

1. Mechanism = **LLM translate + human confirm** (produce → review → commit).
2. Timing = the 3 points (mid-run / at-gate / post-cancel-or-error) **collapse to one
   codepath**: steer only acts on a *not-running* run; a mid-run instruction
   **auto-Stops first**. True live injection stays deferred.
3. Vocabulary = **edit input files + invalidate steps**; downstream cascades via
   input-hash; no magic per-element skip.
4. Continuity = **same `run_id` + same chat** (reuse `decide`'s re-spawn); a terminal
   run flips back to `running` on resume.
5. vs gate = **coexist**; the gate's `approve/reject/revise` is unchanged.
6. Generic = **zero author code, any App**; edit scope = any workspace file *outside*
   `/.workflow/` + invalidate any step.
7. Confirm = **approve / reject / re-instruct** (plan is not inline-editable).

Mechanism details (decided, not separately grilled): always-confirm; the steerer is
read-only + streamed + emits a structured `SteerPlan`; deterministic apply; blast
radius is an honest approximation (the invalidated steps + input diffs + "their
downstream re-runs" — a full pre-simulation is impossible past the first re-run, whose
output the downstream args depend on).

## State machine

- **Steerable** run states: `awaiting_human` (at a gate) · `done` · `error` ·
  `cancelled`. `running` / `pending` → `steer` Stops it first, then proceeds.
- New `WorkflowRun.pending_steer: SteerPlan | None`. While a plan awaits confirm the
  run is `awaiting_human` with `pending_steer` set (and `pending_decision` unset). The
  FE chooses the steer card vs. the gate card by which pending field is set;
  `decide()` still guards on `pending_decision`, so the two never collide.
- `SteerPlan { rationale, input_edits: [{ path, content }], invalidate: [step_name] }`
  — full-content writes (like `agent_write_step`, #107, to dodge tool-arg unreliability).

## Endpoints

- `POST /a/{slug}/items/{itemId}/runs/{runId}/steer { instruction, reasoning_effort? }`
  → (cancel if running) → read-only steerer turn in the run's chat (streamed) →
  parse `SteerPlan` → set `pending_steer`, status `awaiting_human`. `202`.
- `POST /a/{slug}/items/{itemId}/runs/{runId}/steer/confirm { approve }`
  → approve: apply (write edits, delete invalidated artifacts, journal receipt) +
  resume (re-spawn). reject: clear `pending_steer`, restore the prior terminal/awaiting
  state. Re-instruct = call `steer` again.

## Phases (flat)

- **P1** — Docs: un-defer §10 in `docs/workflows.md` + this plan. *(done)*
- **P2** — `SteerPlan` struct + `WorkflowRun.pending_steer` field; journal-relative
  steer receipt path helper. Resource + path tests.
- **P3** — `workflow/steer.py` `propose_steer(wf, instruction, …)`: drive a read-only
  agent turn, parse a `SteerPlan` with tolerant parse + retry-with-feedback. Unit-test
  with a fake `drive_turn`.
- **P4** — `apply_steer(wf, plan, decided_by)`: write `input_edits` (guard: not under
  `/.workflow/`), delete invalidated step artifacts (guard: journal-only), journal an
  audit receipt. Unit-test incl. guard rejections.
- **P5** — `WorkflowOrchestrator.steer()` / `confirm_steer()`: auto-Stop + propose +
  set `pending_steer`; apply + resume re-spawn / discard. Reuse `_spawn` /
  `_read_inputs`. Unit-test with fakes.
- **P6** — API endpoints + typed pydantic request/response models + route tests.
- **P7** — `SteerProposed` event (+ any others) in `api/events.py` /
  `workflow/events.py`; mirror in `web/src/events.ts`.
- **P8** — FE: `SteerConfirmCard` (rationale + file diffs + invalidate/re-run list +
  approve/reject) + run-chat composer reroute to `steer` when the conversation has a
  `run_id` + mid-run auto-Stop banner + pin the card + `useWorkflow` hooks +
  `workflows.ts`. FE TDD (vitest).
- **P9** — Live check (local Ollama): run a topic-hub workflow → Stop mid-run → steer
  "use the X collection" → confirm → verify only the affected step (ingest) re-runs.
  Full suite + 100% coverage gate + ruff/ty.

## Non-goals

True *live* mid-run injection (a note into an already-running node without Stopping);
inline plan editing; a structured-controls UI (the free-text steerer is the path).
