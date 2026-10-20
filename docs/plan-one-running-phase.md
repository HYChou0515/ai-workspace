# Plan — one running phase: the one the execution is in

> Status: problem agreed with the user 2026-10-16; the decisions below are the
> recommendations the user delegated ("我覺得你就把東西修正就行"). Built
> 2026-10-20 in #982 (P1–P4, one commit each); see "As built" below.

## Problem

Reported on `origin/master` (`b695d7db`): in an execution's tab, step 2's
conversation shows under step 1's row, and that row cannot be collapsed.

Reproduced end to end (a user DSL workflow, phases `first` → `second`, one
agent step each, the second held open; `create_app` + HTTP API):

| Moment | `phases[].status` | The tab |
|---|---|---|
| Step 2 running | `first` running, `second` running | step 2's live turn streams in row `first`; `first` is locked open |
| Done | both passed | right: step 2's messages in row `second`, nothing locked |
| Step 2 failed / stopped | `first` running, for good | row `first` can never be collapsed, the run over |

Why, read from `b695d7db`:

- **A phase is never left.** `_apply_progress` sets the phase a step enters to
  `running` (`workflow/orchestrator.py:1492`) and nothing sets the phase it
  left to anything. Only `_post_run` greens phases: at a gate pause, every
  running one but the gate's (`:1037`), and at DONE (`:1054`). So mid-run every
  phase walked so far is running at once, and an error or a Stop freezes them
  running. The two `_post_run` blocks are two copies of one rule.
- **The tab assumes one.** It draws the live turn in the first running row
  (`web/src/components/ExecutionView.tsx:230`, `:440`), keeps every running row
  open (`sticky`, `:43`), and says "Step N of M" from the first running node
  (`web/src/api/workflows.ts` `stepOf`). The messages themselves carry the
  right `phase`; they are drawn in the wrong row only while live, and the lock
  is what outlives the run.
- **The backend half predates #913** (same code at `5aa45d3b`, where it only
  turned several diagram nodes blue). #913's tab made it a wrong row and a
  stuck one. Every workflow with two or more phases hits it: topic-hub
  `memory` and `collections`, playground `echo`, `intake`, `dsl/file-uploads`,
  any user `.workflows/*.json` with two phases.
- **Phases do not overlap, but they repeat.** Steps run one after another; a
  `map`'s parallel elements share one phase. A revise loop goes back:
  `collections` runs classify → review → classify → review_1 (its `run.py`
  `while True` around `agent_step(phase="classify")`), and a DSL gate's
  `revise` re-runs earlier steps. So "the phase being left" is well defined,
  and a phase may be left and entered again.
- **Stored runs keep the wrong value.** A failed or stopped execution already
  in the database has its earlier phases saved as running. Fixing the write
  alone leaves the user's own reported execution stuck.

## Decisions

- **D1 — the invariant.** At most one phase is `running`: the run's
  `current_phase`, and only while the run is live. Entering a phase leaves the
  one that was running, which becomes `passed` (re-entering it later makes it
  `running` again).
- **D2 — a run that ends.** DONE: every running phase is `passed` (as now). A
  gate pause: every running phase but the gate's is `passed` (as now). ERROR:
  the phase it was in is `failed` (a failed step already says so); the others
  `passed`. CANCELLED (Stop): the phase it was in is `stopped`; the others
  `passed`.
- **D3 — which rows stay open.** Unchanged rule, now fed right: a running,
  waiting or failed row is locked open (D7 of #913). A `stopped` row is not
  locked — nothing in it needs the person; they pressed Stop.
- **D4 — one rule, on write and on read.** One pure function,
  `run.settle_phases(...)`, is the only place a phase's status is derived from
  the run's progress. The orchestrator applies it on every write
  (`_apply_progress`, and `_post_run`, replacing its two copies), and the run
  routes apply it to what they serve (`GET …/runs`, `GET …/runs/{id}`), so a
  stored row written before this shows right without a migration.
- **D5 — `stopped` is a new value** of `PhaseState.status`. The tab and the
  diagram name it ("Stopped"); "Step N of M" counts it as reached.

## Phases

- **P1 — the rule, on write.** `settle_phases` in `workflow/run.py`; the
  orchestrator uses it in `_apply_progress` and `_post_run`. Tests over the
  HTTP API with a real two-phase workflow: mid step 2 (`first` passed, `second`
  running), Stop during step 2 (`second` stopped), step 2 fails (`second`
  failed, `first` passed), DONE, a gate pause, and a revise loop re-entering a
  phase.
- **P2 — on read.** The run routes serve settled phases. Test: a stored
  cancelled run with two phases saved `running` is served `passed` +
  `stopped`; a live one is served with only `current_phase` running.
- **P3 — the tab.** "Stopped" label and tone in `ExecutionView`, the phase
  diagram, and `stepOf`; test fixtures take their phases from what the
  orchestrator really writes (the old fixture had `read` passed while `sort`
  ran, a state the orchestrator never produced — why no test caught this).
- **P4 — the runbook.** `docs/migrations.md` entry: `phases[].status` gains
  `stopped`, at most one phase is `running`, stored runs read settled; no
  action, a script reading the field should expect the new value.

## As built (#982)

- **D4's write side moved to the two chokepoints.** A Stop cancels the
  driver, which records `cancelled` and re-raises, so `_post_run` never runs
  for it — settling there would have missed every Stop. Instead both places
  that write a run apply the rule on every write: the orchestrator's `_patch`
  and the driver's (`run.with_settled_phases`). `_apply_progress` and
  `_post_run` no longer touch phase status; `_post_run`'s two copies are gone.
- **A gate needs no special case.** A gate emits `StepStarted` for its phase
  before it pauses (`workflow/gate.py`), so at a pause `current_phase` is the
  gate's phase and "leave the current one as it is" already keeps it.
- **The phase a run ended in is re-read from its last status.** The wall-clock
  cap first cancels the driver (the phase becomes `stopped`) and then records
  `error`; that phase must end `failed`. Only `settle_phases` writes
  `stopped`, so re-reading it is safe.
- **P3's fixture stayed.** The old fixture (`read` passed while `sort` runs) is
  exactly what the orchestrator writes since P1.

## Verification

- Targeted tests, the four gates (`ruff check`, `ruff format --check`,
  `ty check`, `pnpm typecheck`) per phase.
- Every application of `settle_phases` (entry, each `_post_run` branch, each
  route) is mutation-probed: revert that line, watch exactly its test redden.
- Live check in a real browser on the real app (stand-in model only), against
  `b695d7db` as the base: a two-phase workflow mid step 2 (the live turn in row
  2, row 1 collapsible, "Step 2 of 2"), stopped (row 2 "Stopped", collapsible),
  failed (row 1 Done and collapsible, row 2 Failed and open), and a run stored
  before the fix.
