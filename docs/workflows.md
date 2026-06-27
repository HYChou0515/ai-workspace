# Workflows — the manual

> **Status:** normative design spec for issue #100. This document is the *target*
> and the *acceptance criterion*: the implementation is "done" when its observable
> behaviour matches the rules here. Written before the plan, on purpose ("以終為始").
> Decisions were locked through a `/grill-me` session; rejected alternatives are
> recorded inline so we don't relitigate them.
>
> **Authoring a workflow?** This is the *spec*; the practical how-to (block catalog,
> conventions, the `new`/`check` CLI) is [`workflows-authoring.md`](workflows-authoring.md) (#287).

A **workflow** turns the agentic workspace from interactive-only into something an
external system can **trigger over an API** to run **headlessly** to a useful
**artifact** — while reusing the *existing* workspace machinery (sandbox, file
tools, the agent loop, KB) instead of reinventing it.

Two motivating use cases:

1. An external caller periodically hits the API to kick off actions that end in an
   artifact (e.g. a report).
2. Someone uploads files; the system classifies and digests each one and files the
   results into a small, **pre-defined** set of KB collections.

This manual describes the **platform** (the reusable machinery). *How* any one
workflow behaves — what "digest" means, how a file is split, the routing rules — is
**App/profile implementation**, expressed in that profile's code, and out of scope.

---

## 1. Mental model

Think **Temporal**, but the **journal is the filesystem**:

- **Orchestration** = the workflow's `run()` function. It runs **in the backend**,
  owns control flow (sequence / loops / gates), and **re-executes from the top** on
  every (re-)run. It is durable because its progress is recorded as **files**.
- **Nodes** = the units of work `run()` invokes (agent step, deterministic step,
  human gate). A node is an **activity**: it writes its result (an **artifact** or a
  **receipt**) to the workspace under `step_<name>/<key>`, together with an
  **input-hash**. On a later run, a node whose artifact exists **and** whose
  input-hash still matches is **skipped** — its artifact is reused, the work is not
  redone. This is Make-style incremental execution (§9). It is the whole of our
  resume / retry / rewind / crash-recovery story.
- **A workflow run and an interactive workspace are two modes over the same item**,
  sharing the same `ChatTurnEngine` and conversation. An agent node *is* a turn on
  the item — its thinking, messages and tool calls stream into the item's chat
  exactly as if a human were driving. So the run leaves a full transcript + files,
  and a human can take over at essentially zero extra cost (§10).

Why orchestration is in the backend: only a backend driver can re-execute, hold the
sandbox lifecycle, and (later) suspend at a human gate. The sandbox is ephemeral
compute; the **FileStore is the durable record**.

---

## 2. Where a workflow lives

At the **profile** level — **the App level has no orchestration code.**

```
apps/<slug>/
  app.json, model.py, prompts/        # App: the WorkItem type + branding + agent ceiling
  profiles/
    <profile>/
      _profile.json                   # profile config + workflow MANIFEST (phases, input.json path)
      _prompt.md, *.tpl, .skill/      # existing profile assets (prompt, seeded files, skills)
      run.py                          # the orchestration run(wf, inputs)   [backend, trusted]
      nodes/                          # custom deterministic node scripts    [run in sandbox]
```

- A **profile = one complete behaviour package**: prompt + tool subset + seeded
  files + skills + (optionally) a workflow.
- A profile has **0 or 1 workflow**. *With* one → headless-triggerable. *Without* →
  interactive-only. (This is why *creating an item* and *running a workflow* are
  decoupled — §14.)
- `run()` is **trusted backend Python**, discovered by scanning profiles the same
  way Apps are discovered (drop-in → registered). Custom deterministic node scripts
  run **in the sandbox** (§7).
- The profile **seeds** any default files it wants into the workspace using the
  **existing profile file-seeding mechanism** (the same one that seeds `notes.md`,
  `SOP.md`, …). `input.json` (§14) is just one such seeded file.

---

## 3. The authoring model

> **A workflow is a Python `async def run(wf, inputs)` over a small step library,
> plus a small data `MANIFEST`. Control flow is the host language. There is no
> workflow DSL.**

Workflows feel "hard to define" exactly when control flow (loops / branches /
retries / value-passing) is crammed into declarative YAML. Using Python:

| What you need | How you write it |
| --- | --- |
| iterate over items | a plain `for` loop |
| run items concurrently | `asyncio.gather` over per-element work (§11) |
| retry a step with feedback | the step's `retries=` (a `while` under the hood) |
| pass data between steps | plain variables + workspace files |
| branch | a plain `if` |
| "every agent step is gated" | `check=` is a **required** argument of `agent_step` |

**Rejected:** a declarative DAG/DSL. It only wins for visual authoring by
non-engineers or runtime-editable definitions — neither is required — and it brings
back the "hard to define" pain. Observability (§12) does **not** need a DSL.

### Two authoring conventions that make everything else cheap

1. **Pass a step's inputs as its arguments.** Read artifacts in `run()` and feed
   the data into the step; **do not read ambient state inside a step.** This makes a
   step's **input-hash = `hash(its arguments)`** (§9) — trivial to compute, and it
   auto-invalidates downstream when an upstream artifact changes.
2. **Control flow must produce a reproducible *set of step identities*.** Loop
   iteration sets and branch conditions must read only from `inputs` and step
   artifacts — never wall-clock / `random` / a fresh un-stepped query. A step's
   *output* may be fully nondeterministic; only its *identity* (where its artifact
   lands) must be stable, so a re-run lands artifacts on the same paths (§9).

### MANIFEST (the only declarative part)

Lives in `_profile.json`:

```jsonc
{
  // ... existing profile fields ...
  "workflow": {
    "title": "Classify & file uploads into collections",
    "phases": [                                   // the static skeleton for the diagram (§12)
      { "id": "classify", "title": "Classify + digest" },
      { "id": "ingest",   "title": "Ingest to collection" }
    ]
    // input_json omitted ⇒ derives `{profile.upload_dir}/input.json` (§14); pin only to override
  }
}
```

### Authoring surface (illustrative; exact signatures pinned in the plan)

```python
async def run(wf, inputs):
    # wf     — run handle: workspace IO, capability methods, the run-scoped credential
    # inputs — the parsed input.json (content is the profile's own business)
    ...
    return artifact_summary                        # stored on the WorkflowRun (§13)
```

- `wf.read(path)`, `wf.read_json(path)`, `wf.glob(spec)`, `wf.files` — workspace IO.
- `await agent_step(wf, *, prompt, phase, tools=None, check, retries=0, cache=True)` — §5.1.
- `await sandbox_node(wf, *, phase, run, check=None, cache=True)` — custom deterministic node, §5.2.
- `check.*` — gate builders, §6.
- capability methods, e.g. `await wf.ingest_to_collection(collection, path, *, digest=None)` — §8.
- `fail(reason)` / `StepFailed` — abort the current step/element, §6.
- `human_gate(...)` — §10.

---

## 4. Node types

Three kinds, **distinguished by *who invokes them*, not where they run.**

1. **agent node** — an LLM-driven turn (§5.1). The LLM *decides*; it works through
   its tools against the sandbox.
2. **deterministic node** — author code with **no LLM** (§5.2). The orchestration
   *acts*. Runs as a script in the sandbox; reaches platform capabilities over HTTP.
3. **human gate** — suspends for a human decision (§10).

**The decision/action principle (core to reliability):** the LLM only ever
*decides* and records its decision *as data*; the *action* (any side-effect that
must be reliable — ingest, export, …) is performed by a **deterministic node**, not
the agent. The agent never holds the tool that performs the side-effect (§7).

---

## 5. Node details

### 5.1 Agent node

- Runs through the **existing `ChatTurnEngine`** (backend loop; its `exec`/file
  tools act on the sandbox). It is a normal turn on the item → persisted as
  `Message`s and streamed over SSE. This is what gives transcript continuity and
  free human takeover (§10).
- **`tools=` ⊆ the profile's tool ceiling** (the LLM-safety boundary; coherence
  enforced like #89's `validate_function_coherence`). Agent tools skew toward
  **read / explore**; side-effects are deterministic nodes, not tools (§7).
- **`check=` is mandatory.** An agent node with no gate is a schema error.
- **Artifact:** the step writes its output to `step_<name>/<key>` and is skipped on
  re-run if that exists with a matching input-hash (§9). A failed gate (after
  `retries`) means **no artifact is written**, so a re-run retries it.

### 5.2 Deterministic node

- **No LLM.** Author code (a script under `nodes/`), runs **in the sandbox**. Needs
  a platform capability (ingest, KB read, …)? It calls the capability's **HTTP
  endpoint** with the **run-scoped credential** (§8, §15). It does not import
  backend internals.
- Not exposed to the LLM; not governed by the tool subset (§7).
- **Artifact / receipt:** every deterministic node must record a result under
  `step_<name>/<key>` so it is checkpointable — even when its real effect lives
  elsewhere (e.g. an ingest writes a `step_ingest/<file>.done` receipt with the doc
  id), so it can be skipped on re-run.

---

## 6. Gates / checks

- **Every agent node has a gate** (§5.1); deterministic nodes are their own check
  (do, then verify).
- **Deterministic checks are primary; LLM-judge checks are auxiliary.** Verify
  mechanically wherever possible (file non-empty; chosen value ∈ allowed set; doc
  actually landed in a collection). Reserve an LLM-judge check (another agent turn
  returning pass/fail) for goals only semantically checkable. A deterministic
  predicate is a hard guarantee; an LLM judging an LLM is one unreliable thing
  checking another.
- **On failure: retry-with-feedback `N` times, then abort the step.** The check's
  failure reason is fed back into the *same step's* re-run (an in-step loop, within
  one run). After `N` attempts the step aborts; in a loop the per-element policy is
  **skip + collect** by default (§11). Because a failed step writes no artifact, a
  *later* run also retries it.
- Built-in checks (illustrative): `check.file_nonempty(path)`,
  `check.choice_in(path, key, allowed)`, `check.collection_has(collection, path)`,
  `check.exec(cmd)`, `check.llm_judge(criteria)`.

---

## 7. Tools vs deterministic node scripts

Both can run in the sandbox; that is **not** what separates them — **the invoker
is.**

| | **agent tool** (incl. tool packages) | **deterministic node script** |
| --- | --- | --- |
| invoked by | the **LLM** (an agent node) | the **orchestration** (`run()`) |
| needs an LLM schema | yes | no |
| visible to the LLM | yes | no |
| bounded by | the profile's **tool subset** | the **run-scoped credential's capability scope** |
| why bounded that way | the LLM is unpredictable → safety boundary | author code is fixed → authz over which capabilities it may hit |

- The **tool subset governs only the LLM.** Deterministic nodes are not in it and
  not constrained by it.
- The shared layer is the **sandbox + the capabilities** (§8), not the invocation
  surface. A tool package can be reached by both paths; a deterministic node need
  not be a package (it can be a plain command, e.g. a gate's `test -s report.md`).
- **Consequence:** any reliable side-effect (ingest, export) is a **deterministic
  node**, never an agent tool. The agent's subset stays read/explore-shaped — so
  "avoid false completion" is stronger than a post-hoc gate: **the agent doesn't
  hold the tool that could botch the step.**

---

## 8. Capabilities (HTTP) & the decision/action pattern

- Platform operations (KB ingest, KB query, …) are **HTTP endpoints**. Sandbox code
  calls them with the **run-scoped credential** (§15). The same endpoints can serve
  external callers and, where wanted, be wrapped as agent tools.
- **`ingest_to_collection(collection, path, *, digest=None)`** — reuses the existing
  `Ingestor.store` + `index` under `rm.using(user=<captured>)`, awaits `ready`.
  - **Idempotent**: the SourceDoc id is `encode_doc_id(collection, path)` → re-ingest
    is an **upsert**, never a duplicate (safe under re-run).
  - **Requires the collection to exist** (no auto-create).
  - Writes a `step_ingest/<file>.done` receipt; the matching gate
    `check.collection_has(collection, path)` reads the doc back and asserts `ready`.

### Decision/action for parameterised side-effects

When the LLM must *influence* a reliable side-effect ("send this file to collection
X"), it does **not** call the API. It records the parameter as **data**; a
deterministic node carries it to the capability:

```python
# agent node: LLM decides; records parameter X as data (write_file is in its subset)
await agent_step(
    wf, phase="classify",
    prompt=f"Read {f}. Pick its collection from {allowed}. "
           f"Write {{collection, digest}} to plan/{f}.json.",
    tools=["read_file", "write_file"],                       # NO ingest tool
    check=check.choice_in(f"plan/{f}.json", key="collection", allowed=allowed),
    retries=2,                                               # invalid X → feedback → re-pick
)
# deterministic node: orchestration carries X to the capability (LLM not involved)
plan = wf.read_json(f"plan/{f}.json")
await wf.ingest_to_collection(plan["collection"], f, digest=plan["digest"], phase="ingest")
```

X (`plan["collection"]`) travels **LLM → file → deterministic node → capability**.
"The LLM carries a parameter" does **not** mean "the LLM calls the API." The gate
clamps X to the allowed set; the node guarantees exactly-once.

---

## 9. Execution model — the filesystem *is* the journal

This is the heart of the design. It replaces any separate journal/replay machinery.

- **Each step checkpoints to the workspace** under the run's **journal home**
  `/.workflow/<workflow_id>/` (legacy singular workflows → `/.workflow/_default/`), at
  `/.workflow/<workflow_id>/step_<name>/<key>` (key = loop element / call identity,
  e.g. `/.workflow/collections/step_classify/file_7.json`), alongside its
  **input-hash** (`= hash(the step's arguments)`, per the §3 convention). The journal
  lives in its own folder so it stops scattering across the workspace root, and each
  workflow's `step_*` artifacts stay grouped under that workflow's folder (#136). The
  bare `step_<name>/<key>` shorthand used elsewhere in this doc always means that path
  *inside* the run's journal home.
- **On-demand inline skip.** A run re-executes `run()` from the top. **When control
  flow reaches a step**, the step first checks its own artifact: if it exists **and**
  the input-hash still matches → **skip** (return the cached artifact, do not redo
  the work, do not re-call the LLM, do not re-post to chat). Otherwise → **execute**,
  then write the artifact + input-hash. There is no up-front scan — checks are cheap
  and inline.
- **Auto-invalidation.** Because input-hash = `hash(args)`, editing an upstream
  artifact changes what `run()` passes downstream → the downstream step's hash no
  longer matches → it re-runs. **Editing upstream automatically re-runs the affected
  downstream** — no manual bookkeeping.
- **`cache=False` (never-cache).** A step can opt to always re-run (or it naturally
  always re-runs because its inputs always change, e.g. "fetch latest"). Its
  downstream re-runs too, correctly.
- **Determinism is about *identity*, not output.** Step outputs may be fully
  nondeterministic (LLM). What must be reproducible is the *set of step identities*
  — guaranteed by the §3 control-flow convention (iterate stable sets, branch on
  inputs/artifacts only).

What this single mechanism gives us, for free:

- **Resume / crash / restart recovery** — re-run; completed steps skip (artifacts
  live in the persistent FileStore).
- **Retry / rewind** — **stop the run, edit or delete artifacts in the normal file
  UI, press Run again.** Deleting `step_X/<key>` forces step X to re-run; editing an
  upstream artifact re-runs its downstream via input-hash. No rewind API, no
  `retry_to` list, no positional-prefix rule. *(All of these earlier mechanisms are
  removed — superseded by this.)*
- **Reset "from scratch"** — delete the run's journal folder `/.workflow/<workflow_id>/`
  (or the `step_*` artifacts within it); keep the inputs. The #52 per-turn-snapshot
  dependency is no longer required for this.

---

## 10. Human interaction & takeover

A run and an interactive workspace are **two modes of one item, one
`ChatTurnEngine`, one conversation**. Each agent node is a turn on that item, so a
run leaves a full transcript + files.

- **Stop & take over.** At any time a human can **Stop** the run (a control
  action, not a chat message; reuses `cancel_current`). The run goes terminal; the
  item opens to interactive use; the human continues from the current files +
  transcript. For a parallel batch (§11) in-flight elements are cancelled; completed
  ones (idempotent, already committed) are kept and reported. This is the escape
  hatch when an agent goes off the rails: stop, inspect, edit artifacts, re-run.
- **While `running`, the item is workflow-driven** — a human does not free-chat into
  the same turn queue. Free chat opens once the run is terminal (or `awaiting_human`).
- **Human gate (v1).** `await human_gate(wf, phase, title, summary, allow)`
  suspends the run and records a **pending decision** (its result is just another
  **artifact**, `step_<gate>/decision.json` inside the run's journal home — i.e.
  `/.workflow/<workflow_id>/step_<gate>/decision.json`). The run stops; a human responds via
  `POST .../runs/{id}/decisions` with `{choice, input?}`; re-running finds the decision
  artifact, the gate reads it, and execution continues. `allow` lists the choices the
  FE offers; a `revise` choice reveals a free-text `input` the body can act on. Outcomes
  the body sees: `approve` / `reject` (→ end + interactive takeover) / `revise` (+ input,
  e.g. `→collections` regenerates its drafts from the note). **"Retry/rewind" is not a
  gate outcome** — it is the file-based mechanism in §9 (delete artifacts + re-run).
  - **Why v1:** both use cases end in an irreversible commit (publish a report;
    write into collections) that **must be confirmed first**. The canonical shape is
    **produce → review → commit**: the agent produces reviewable artifacts (safe),
    a `human_gate` lets a human approve, and only then does a deterministic node
    commit the side-effect. Because the gate sits *before* the commit, a `reject`
    leaves nothing committed.
- **Steer-and-resume (#288).** Past a run's active window — at a gate, or once it is
  terminal (`done` / `error` / `cancelled`) — a human can **redirect the run in words**
  instead of hand-editing files (§9). They type a free-text instruction in the run's
  chat (e.g. *"use the a, b collections and redo the upload"*); a read-only **steerer**
  turn reads the current inputs + journal + transcript and proposes a **steer plan**:
  which input files to rewrite and which steps to **invalidate** (delete the artifact →
  force re-run). The plan is **reviewed before it applies** (produce → review → commit,
  the same shape as a gate): a confirm card shows the file diffs + which steps will
  re-run vs. be kept (the blast radius), and the human **approves / rejects / re-instructs**.
  On approve a deterministic step applies the edits + deletes the invalidated artifacts,
  and the **same run resumes** (§9 re-run: completed steps whose input-hash still matches
  **skip** — *incremental*, the expensive prefix is not redone). A mid-run instruction
  first **Stops** the run (the in-flight node would re-run on resume anyway), then steers.
  - **Vocabulary = edit inputs + invalidate steps** — the two generic moves; downstream
    re-runs cascade through input-hash (§9). It is platform-level and needs **no author
    code**: the steerer may rewrite any workspace file *outside* the journal
    (`/.workflow/`) and invalidate any step. The LLM only *proposes* (decision); the
    deterministic apply *acts* (action) — the decision/action split (§8) again. Steering
    **coexists** with a gate's `approve` / `reject` / `revise`: those are the author's
    in-body outcomes; the steerer is the always-available free-text path on top.
  - **Endpoints:** `POST .../runs/{id}/steer {instruction}` (Stops the run if running →
    runs the steerer → sets `pending_steer`, run goes `awaiting_human`);
    `POST .../runs/{id}/steer/confirm {approve}` (approve → apply + resume; reject →
    discard the plan; re-instruct → call `steer` again with a new instruction). The
    proposed plan + the human's answer are journaled under `/.workflow/<workflow_id>/steer/`
    for audit.
  - **Still deferred:** true *live* injection (a note delivered into an already-running
    node, without Stopping) — not needed; Stop-then-steer covers the cases.

---

## 11. Control flow

- Primitives: **sequence**, **`for`-each** (plain Python), in-step **retry**
  (`retries=`), and cross-run resume (§9).
- **Parallel for-each is in v1.** `async`/`gather` makes the *orchestration*
  concurrent for free — but it does **not** parallelise agent turns by itself,
  because the reused machinery is **serial-per-item**: `ChatTurnEngine` is
  FIFO-per-key, one sandbox per item, one chat per item. True parallelism therefore
  fans each element out to its **own turn-key + ephemeral sandbox** (and its own
  chat sub-stream), bounded by the **global concurrency cap** (§16); the parent
  `gather`s and aggregates.
  - **Consequence (accepted):** a batch run produces **per-element sub-logs**, not
    one merged conversation — the right shape for batch (per-file status + an
    aggregate, not an N-way interleaved chat). The "one readable chat" model is for
    interactive / single-track runs.
  - Deterministic / pure-I/O nodes (e.g. an ingest HTTP call) don't touch the engine
    and can be `gather`ed cheaply.
- **No branching primitive.** Branch with a plain `if` on inputs/artifacts; route
  with data (a step writes a plan, a loop consumes it) rather than control-flow
  branches.

---

## 12. Observability (phase-level)

- We support **phase-level observation** ("where is the run / which phase broke"),
  **not** node-level visual authoring.
- The diagram = a **static phase skeleton** (`MANIFEST.workflow.phases`, known
  before running) **+ live step events** overlaid. Each step carries `phase=`;
  dynamic detail (loop progress, retries, skips) shows up *under* a phase node
  ("12/20, 1 failed"), not as pre-drawn per-element nodes.
- **Live** = SSE (extend `api/events.py`, mirror in `web/src/events.ts`:
  `PhaseEntered` / `StepStarted` / `StepPassed` / `StepFailed` / `StepSkipped` /
  `StepRetrying` / `AwaitingHuman`). **Historical** = query the `WorkflowRun`.
- **Caveat (don't oversell):** the skeleton is the *declared* set of phases; if the
  code skips/reorders phases for some input, the diagram can drift. Keep phases
  coarse and mostly-linear; mark phases that didn't run as skipped.

---

## 13. WorkflowRun (persisted resource)

A new specstar resource makes "where / what broke" answerable live and after the
fact, and runs listable. The **filesystem is the journal** (§9), so `WorkflowRun`
holds *status*, not the step results:

- `status`: `pending | running | awaiting_human | done | error` (+ `cancelled`)
- `current_phase`, per-phase status / progress, `failures` (collected per-element)
- `item_id`, `captured_user`, `started`/`ended`, `result` (the `run()` return value)
- `pending_decision` (+ who decided) — set while `awaiting_human` at a gate
- `pending_steer` — set while `awaiting_human` for a steer plan awaiting confirm (#288);
  the FE renders the steer confirm card vs. the gate card by which pending field is set

---

## 14. Trigger & API

**The platform's input surface is exactly two things.** Everything else (the input
folder, the `input.json` content, how files are laid out) is the **profile's**
business, using the existing free workspace.

1. **Config: where `input.json` is** (`MANIFEST.workflow.input_json`). The platform
   surfaces this file's parsed content to `run()` as `inputs`; it does not validate
   it or mandate its shape. **Omit it** (#198) and the platform derives
   `{profile.upload_dir}/input.json` — the same staging folder a chat attach lands in
   (`upload_dir` defaults to `uploads`), so attach and the workflow that consumes the
   files never drift; pin an explicit path only to override. The profile **seeds** a
   default `input.json` (e.g. `{"files":["uploads/*"],"except":["uploads/input.json"]}`);
   a human may freely edit it like any file before pressing Run — **the pre-run
   workspace behaves exactly like a non-workflow item.**
2. **Run** — `POST /a/{slug}/items/{item_id}/run` (async; API-triggerable). Starts
   the orchestrator on the item; the run reads `inputs` + the workspace as the
   profile dictates. The body is optional:
   - **empty body** (`?workflow_id=…` query only) — the plain trigger the UI makes;
     it runs against whatever already sits in the workspace.
   - **`multipart/form-data`** (#197) — an external trigger uploads the workflow's
     input **files in the same call**, because we talk to workflows through the
     workspace, not a JSON body. Each `file` part's **filename IS its workspace path**
     (sub-dirs allowed, e.g. `inputs/data.csv`); `workflow_id` may ride as a query
     param **or** a form field (query wins). The files are written (overwrite,
     last-write-wins) **before** the run starts; a path escaping the workspace root
     aborts the whole call with **400** (nothing half-written, no run). There is **no
     `input.json` in the request** — if a workflow wants one, it is simply one of the
     uploaded files.

**There is no separate "manual vs auto" mode** — both reduce to *prepare the item's
inputs, then Run*:

- **Human:** open a workflow-profile item, drop files / edit `input.json` via the
  file UI, press **Run workflow**.
- **External / periodic:** create an item, then **Run with the input files attached**
  to the same multipart call (above) — one self-contained trigger. (Uploading files
  via the existing file routes first and then calling Run with an empty body is
  equivalent; the multipart form is the convenience.)

After Run, the platform does exactly two things: **the orchestrator updates the
`WorkflowRun` status**, and **agent nodes stream into the item's chat as if talking
to a user** (§1, §5.1).

- **Poll:** `GET /a/{slug}/items/{item_id}/runs/{run_id}` → status + result + per-phase progress + failures.
- **Stream:** `GET .../runs/{run_id}/stream` → SSE (reuses `subscribe_sse`).
- **Decide:** `POST .../runs/{run_id}/decisions` → `{ choice, input? }`.
- **Discover:** `GET /a/{slug}/profiles` lists profiles, flags which have a workflow,
  returns each `MANIFEST` (title / phases) so the FE can render the Run affordance.
- **An item may host multiple sequential runs** (prepare → run → prepare more →
  run again); at most **one active run per item** at a time.
- Artifacts are workspace files, fetched via the item's existing file routes.

---

## 15. Identity & auth

- **Reuse the existing `get_user` seam** at the trigger boundary; production swaps a
  real implementation behind it (no separate token mechanism is built here).
- **Capture the acting user at trigger time** on the `WorkflowRun`. Background steps
  (and any re-run) have no request context, so they act under
  `rm.using(user=<captured>)` — `created_by`, KB ingestion attribution, and
  notifications stay correct (same pattern as the index/wiki job pods).
- **Run-scoped credential:** sandbox code that calls capabilities over HTTP gets an
  ephemeral credential injected into its env; it maps to the captured user, is scoped
  to that run's allowed capabilities, and expires when the run ends.
- **Gate approval:** any authenticated human with access may act; **who acted is
  recorded.**

---

## 16. Lifecycle & resources

- **Sandbox:** lazily created on first `exec`. Released on **terminal** (reusing
  `registry.close_session` + `turn_engine.forget`) and on **`awaiting_human`** (a
  pause may last days; the FileStore persists files, so resume recreates the sandbox
  lazily). Parallel for-each uses **per-element ephemeral sandboxes** (§11).
- **Items:** a run's terminal **does not auto-close** the item (it stays a workspace
  for inspection / re-run / takeover). API-created items accumulate → cleaned by a
  **TTL / keep-last-K** retention setting, or closed by a human.
- **Concurrency:** a **global cap** on concurrent runs + sandboxes (covers parallel
  for-each elements too); excess queues (`pending`). The cap is a config setting.

---

## 17. Robustness (headless)

- **Timeouts:** per-step (max duration per agent turn) **and** a per-run wall-clock
  cap; exceeding either aborts to `error`, recorded on the `WorkflowRun`.
- **Budget:** a per-run **max-steps** hard ceiling (guards infinite loops) and an
  optional token/cost budget.
- **Failure notification (v1):** **pull** (poll the `WorkflowRun`: `error` + which
  phase + why) **+** in-app notification to owner / watchers (existing mechanism). An
  outbound **webhook** is deferred.

---

## 18. Versioning

- **input-hash carries most of the weight.** Because a step's hash includes its
  arguments (which include the resolved prompt), **editing the workflow re-runs the
  steps it affected** on the next run, and leaves unaffected steps cached. This is a
  bonus of §9, not extra machinery.
- **Breaking changes are made by adding a new profile**, not mutating one in place.
  A profile is treated as an **immutable behaviour version**: ship `profile-v2`;
  existing items keep pointing at the untouched old profile. No true module-level
  version pinning is built.

---

## 19. Platform vs App boundary

- **Platform = bricks + enforcement + two interfaces:** `agent_step`, deterministic
  nodes, mandatory gates, `for`/parallel/retry, the §9 filesystem-journal +
  input-hash skip, the `WorkflowRun`, capabilities (`ingest_to_collection`, …), the
  run-scoped credential, the allowed-set clamp (a deterministic gate), the
  `input.json` location config, and the Run endpoint.
- **An App/profile composes those bricks** with its prompts, rules, `input.json`
  layout, and node scripts. *How* a workflow behaves (split / digest / routing) is
  App implementation, out of scope here.

---

## 20. Worked example — the "intake" App (illustrative)

Use case 2 as a profile-level workflow, showing the canonical **produce → review →
commit** shape. The collection set is **pre-defined in the profile**; the seeded
`input.json` says "files are in the profile's `upload_dir`" (default `uploads/`, the
same folder a chat attach lands in, #198); the user drops files and presses Run.

```python
async def run(wf, inputs):
    allowed = wf.config["collections"]              # pre-defined in the profile (not per-run)
    files = wf.glob(inputs)                          # inputs = parsed input.json (the file spec)

    # Phase 1 — PRODUCE: classify+digest every file. Safe — only writes plan/<f>.json. Parallel.
    async def classify(f):
        await agent_step(                            # agent node: DECISION recorded as data
            wf, phase="classify",
            prompt=f"Read {f}. Split per your profile; for each piece pick a collection "
                   f"from {allowed} and write a digest. Record the plan in plan/{f}.json.",
            tools=["read_file", "write_file"],       # no ingest tool — agent can't commit
            check=check.choice_in(f"plan/{f}.json", key="collection", allowed=allowed),
            retries=2,
        )                                            # → step_classify/<f>.json (skipped on re-run if unchanged)
    await wf.map(classify, files)                    # parallel for-each, bounded by the concurrency cap (§11)

    # Phase 2 — REVIEW: the human confirms BEFORE anything is committed to KB.
    plan = {f: wf.read_json(f"plan/{f}.json") for f in files}
    decision = await human_gate(
        wf, phase="review",
        title="Approve filing these into collections?",
        summary=plan,                                # the human reviews the whole routing plan
        allow=["approve", "reject"],
    )
    if decision.choice == "reject":
        return {"status": "rejected"}                # nothing committed; item stays interactive for takeover

    # Phase 3 — COMMIT: deterministic, idempotent. Only runs after approval.
    failures = []
    async def commit(f):
        try:
            for piece in plan[f]:
                await wf.ingest_to_collection(piece["collection"], piece["out_path"],
                                              digest=piece["digest"], phase="ingest")
                await check.collection_has(piece["collection"], piece["out_path"])
        except StepFailed as e:                      # per-element policy: skip + collect
            failures.append({"file": f, "error": str(e)})
    await wf.map(commit, files)
    return {"processed": len(files) - len(failures), "failures": failures}
```

A human who spots a bad result **stops the run, deletes or edits `step_classify/<f>.json`
(or `plan/<f>.json`) in the file UI, and presses Run** — only the affected files
re-run (§9). And nothing reaches a collection until the **review** gate is approved.

---

## 21. Phasing & non-goals

The full manual is the target. The build is sequenced (foundations first), but it is
**one v1 scope** — **`human_gate` is in v1**, because both use cases end in an
irreversible commit (publish a report; write into collections) that must be
confirmed first (§10). The filesystem-journal (§9) makes durability *and* the gate
cheap, so there is no reason to split them out.

**v1 (everything the two use cases need):**
agent + deterministic nodes; mandatory gates with in-step retry-with-feedback;
`for`-each **and parallel for-each** (per-element sandbox + concurrency cap, skip +
collect); the **filesystem-journal + input-hash** execution model (resume / retry /
rewind / reset / crash-recovery, `cache=False`); **`human_gate`** (produce → review →
commit; decision-as-artifact, `awaiting_human`, the **decisions** endpoint, sandbox
release on pause); **Stop & take over**; phase-level diagram + events; `WorkflowRun`
status; the **Run** endpoint + Discover + Poll + Stream; `input.json` location config
+ profile seeding; `get_user` identity + captured-user + run-scoped credential;
sandbox lifecycle; concurrency cap; timeouts + max-steps; pull + in-app failure
notify; `ingest_to_collection`.

(Build order: the gate lands late in the sequence since it depends on the engine +
`WorkflowRun`, but it is in scope — the v1 gate is not "done" without it.)

**Deferred / non-goals:** conversational steer-and-resume **landed in #288** (§10) —
only *true live* mid-run injection (a note into an already-running node, without
Stopping) stays deferred; declarative-DAG
authoring; node-level visual editing; control-flow branching primitives (use data);
outbound webhook callbacks; true module-level version pinning (use the new-profile
convention); real SSO authz; LLM-judge checks as anything more than an occasional
escape hatch.
