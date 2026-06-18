# Plan — Workflows (#100)

> Implements `docs/workflows.md` (the manual = the spec + acceptance criterion).
> Read the manual first. This plan only sequences the build; if it disagrees with
> the manual, the manual wins. Follow `/tdd` per phase; FE code also follows `/tdd`
> (vitest). Gate at the end with the full suite + 100% coverage (no pipe-mask),
> `ruff`, `ty`, FE typecheck + build, and a **live canned check** of a real run.

## Phasing

**One v1 scope.** Both use cases end in an irreversible commit (publish report; write
into collections) that must be confirmed first, so **`human_gate` is in v1** (manual
§10, §21). The build is *sequenced* (foundations → engine → endpoints → human_gate →
FE → gate), but nothing is "done" until the produce → review → commit path works
end-to-end. Only steer-and-resume + the other non-goals are deferred.

## Key existing seams to hook (don't rebuild)

- **`api/turns.py` `ChatTurnEngine`** — `enqueue(key, content, ctx, on_complete) ->
  Future`. An **agent node** = `enqueue` a turn, `await` the future, capture the
  produced `TurnMessage`s in `on_complete`, then write the artifact. FIFO-per-key
  gives serial-per-item; parallel for-each uses distinct keys (§11).
- **`apps/` profiles** — `apps/profiles.py` loader + `AppCatalog` 3-layer resolve
  (#89). Workflow lives in a profile; `run.py` + `MANIFEST` in `_profile.json`.
  Discover by scanning profiles (mirror `registry._app_models()` scan).
- **`apps/registry.py`** scan pattern — drop-in discovery for workflow profiles.
- **`kb/ingest.py` `Ingestor.store` + `index`**, **`kb/doc_id.encode_doc_id`** —
  reuse verbatim for `ingest_to_collection` (idempotent upsert).
- **`api/events.py` + `web/src/events.ts`** — add the step/phase events; keep in sync.
- **`InvestigationRegistry`** (sandbox lifecycle) — per-item sandbox; extend for
  per-element ephemeral sandboxes + `close_session` teardown.
- **`rm.using(user=…)`** — acting-user for background steps (job-pod pattern).
- **`factories.create_app`** — wire the workflow registry + Run routes + validation.

---

## Phases

### P1 — `WorkflowRun` resource
- specstar `WorkflowRun` Struct (manual §13): `status` enum, `current_phase`,
  per-phase progress, `failures`, `item_id`, `captured_user`, `started`/`ended`,
  `result`. `INDEXED_FIELDS` = `["item_id", "status"]`. Register in `make_spec`.
- TDD: create/read; status transitions; list-by-item (one item → N runs); aggregate
  scoped by item (not global group-by, per `reference_specstar_indexed_queries`).

### P2 — step library + filesystem-journal engine (the core)
- `wf` run handle: `read`/`read_json`/`glob`/`files` (over the item's FileStore),
  `config` (profile config), the captured user, the run-scoped credential handle.
- **`agent_step`** — builds the `AgentToolContext` (profile tools⊆, item key),
  `enqueue`s a turn, awaits, runs the `check`; on fail feeds the reason back and
  retries up to `retries`; on success writes `step_<name>/<key>` + **input-hash =
  `hash(args)`**. **Mandatory `check=`** (a call without it is a type/validation error).
- **`sandbox_node`** — dispatches a script/command into the sandbox, captures
  result, runs `check`, writes artifact/receipt + input-hash.
- **on-demand skip** — before executing any step: if `step_<name>/<key>` exists AND
  its recorded input-hash matches → return cached artifact (no run, no LLM, no chat
  post). `cache=False` ⇒ always run.
- `check.*` builders (`file_nonempty`, `choice_in`, `collection_has`, `exec`),
  `fail` / `StepFailed`.
- TDD anchors: skip-on-rerun (artifact present + hash match); re-run on hash change
  (edited upstream → downstream re-runs); deleted artifact → re-run; `cache=False`
  always runs; mandatory-gate enforcement; retry-with-feedback then abort; agent
  node NOT re-calling the LLM on skip (assert with `ScriptedAgentRunner`).

### P3 — profile workflow discovery + MANIFEST
- Extend the profile loader: a profile with `run.py` + `workflow` in `_profile.json`
  is a workflow profile. Parse `phases` + `input_json` path. Surface in
  `GET /a/{slug}/profiles` (flag has-workflow + return MANIFEST).
- Coherence check at startup (extend `validate_all_apps`): `run.py` importable,
  phases well-formed, agent_step tools ⊆ profile ceiling.
- TDD: discovery (drop-in profile registered), MANIFEST surfaced, coherence errors
  fail loud at startup; `_`-prefixed skipped (mirror app scan).

### P4 — Run endpoint + orchestration driver
- `POST /a/{slug}/items/{item_id}/run` — validate item under slug (shared `Depends`,
  #95), load the profile's `run`, create a `WorkflowRun` (capture `get_user`), start
  the orchestration as a background task, return `{ run_id, item_id }`. Enforce **one
  active run per item**.
- The driver: reads `input_json` → passes parsed `inputs` to `run(wf, inputs)`;
  updates `WorkflowRun.status`/`current_phase` as phases enter; persists `result` /
  `error` on terminal.
- `GET .../runs/{run_id}` (poll), `GET .../runs/{run_id}/stream` (reuse
  `subscribe_sse`).
- TDD (ScriptedAgentRunner): happy-path run → `done` + result; failing step → `error`
  + phase + reason; re-run skips completed; second run on same item; reject double
  active run.

### P5 — `ingest_to_collection` capability + run-scoped credential
- HTTP capability endpoint: read workspace file → `rm.using(captured)` →
  `Ingestor.store` + `index` → await `ready`; idempotent via `encode_doc_id`;
  require collection exists (404 else). Writes `step_ingest/<file>.done` receipt.
- Run-scoped credential: minted at run start, injected into the sandbox env, maps to
  captured user, scoped + expiring. Sandbox node auths capability calls with it.
- TDD: ingest lands a `ready` doc; re-ingest upserts (no dup); unknown collection →
  fail; `collection_has` gate; credential scope + expiry (reject after terminal).
  (The full live check lands in P13.)

### P6 — parallel for-each (`wf.map`)
- `wf.map(fn, items, *, concurrency=cap)` — per-element **own turn-key + ephemeral
  sandbox**; bounded by the global cap; skip+collect per element; aggregate. Parent
  serial sections still use the item's main key/sandbox.
- TDD: N elements run concurrently (distinct keys, not serialized); one failing
  element collected, others complete; per-element artifacts don't collide; cap
  bounds concurrency.

### P7 — observability events
- Add `PhaseEntered` / `StepStarted` / `StepPassed` / `StepFailed` / `StepSkipped` /
  `StepRetrying` to `api/events.py`; mirror in `web/src/events.ts`. Engine emits them
  + updates `WorkflowRun` per-phase progress.
- TDD: events emitted in order; skip emits `StepSkipped`; failure carries phase +
  reason; `WorkflowRun` progress matches the stream.

### P8 — robustness
- Per-step timeout + per-run wall-clock cap; per-run max-steps; optional token
  budget → abort to `error`. Failure notify: pull (status) + in-app notify owner.
- TDD: step timeout aborts; max-steps trips; notify fires on error.

### P9 — lifecycle & resources
- Sandbox released on terminal (`close_session` + `turn_engine.forget`); per-element
  sandboxes torn down; TTL / keep-last-K retention sweep for API-created items;
  global concurrency cap (runs queue when full).
- TDD: terminal releases sandbox; cap queues excess; TTL prunes; terminal does NOT
  auto-close the item.

### P10 — Stop & take over
- `POST .../runs/{run_id}/cancel` (or reuse Stop) → `cancel_current` → run terminal
  (`cancelled`); item opens to interactive; parallel in-flight elements cancelled,
  completed kept. Free chat opens post-terminal.
- TDD: stop mid-run → cancelled + partial kept; chat usable after; re-run resumes
  from artifacts.

### P11 — `human_gate` + decisions
- `human_gate(...)`: suspend the run (status `awaiting_human`), write
  `pending_decision` on the `WorkflowRun`, release the sandbox; the run task exits.
  Decision recorded as artifact `step_<gate>/decision.json`.
- `POST .../runs/{run_id}/decisions` → write the decision artifact → resume (re-run;
  completed steps skip; the gate reads the decision artifact and continues).
- Outcomes the body sees: `approve` / `reject` (→ terminal + interactive) /
  `revise`(+input). (Retry/rewind stays the §9 file mechanism, not a gate outcome.)
- In scope because both use cases gate **before** their irreversible commit (manual
  §10, §21): produce → review → commit.
- TDD: gate suspends → `awaiting_human`; decision resumes; approve continues; reject
  → terminal + interactive takeover; sandbox released on pause + recreated on resume.

### P12 — Frontend — `/tdd` + vitest
- **Discover/Run:** on a workflow-profile item show **Run workflow**; prepare inputs
  via the existing file UI (drop into `inputs/`, edit `input.json`); Run → `POST
  …/run`; hooks via TanStack Query (keys in `queryKeys.ts`).
- **Run view:** phase diagram (skeleton from MANIFEST.phases + live events;
  current/passed/failed/skipped states), `WorkflowRun` status, **Stop** button,
  per-element batch sub-logs. **Reuse `AgentEntryView`** for agent-node
  reasoning/tool cards (the chat stream is the same SSE).
- **Decision card** on `awaiting_human`: render `pending_decision`, post
  approve / reject / revise(+input) to the decisions endpoint, resume the view.
- **Run list:** per-item run history (status, started/ended, result/failures), link
  to a past run.
- TDD: Run button gated on has-workflow; diagram renders skeleton + applies events;
  stop wired; batch sub-logs; decision card posts + resumes; provider-wrapped
  (`renderWithQuery`).

### P13 — v1 gate
- Full backend suite + 100% coverage (no pipe-mask, read `N failed`); `ruff`; `ty`
  (changed files clean); FE typecheck + `pnpm build` + vitest. Commit local only.
- **Live canned check** (DoD, per `feedback_llm_features_need_live_checks`) against
  local Ollama: the full **produce → review → commit** path — trigger → classify →
  `awaiting_human` → approve → ingest → poll `done` — plus a `reject` leaving nothing
  committed.

---

## Deferred (not in v1)

- **Steer-and-resume** — queue a human note mid-run, injected into the next node's
  context at the boundary. (Not needed by the two use cases.)
- Plus the manual §21 non-goals: declarative-DAG / visual authoring; control-flow
  branching primitives; outbound webhook callbacks; module-level version pinning;
  real SSO authz.

---

## Risks / watch-list (from the grill)

- **Determinism of step identity** (manual §3/§9): the sharpest author footgun. Lint
  / document: control flow reads only `inputs` + artifacts; inputs passed as args.
- **Parallel for-each resource use** — per-element sandboxes multiply; the cap is the
  only thing between a big batch and resource exhaustion. Make it real, not nominal.
- **Phase skeleton vs dynamic execution** (§12) — keep phases coarse; mark skipped.
- **input.json has no platform validation** (§14) — bad input fails inside `run()`;
  acceptable, but the worked workflows should gate their own inputs early.
