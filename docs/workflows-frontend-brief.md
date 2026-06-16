# Frontend design brief — Workflows (#100)

> A brief to hand to a frontend-design assistant. It states **what to design and the
> functional/UX requirements**, and what to **reuse** from the existing app — it does
> **not** prescribe the visual design; bring your own craft. The authoritative
> behaviour spec is `docs/workflows.md`; this brief translates it into UI needs.

## 1. Product context (for a designer with no prior knowledge)

The app is a React + Vite SPA over a FastAPI backend. It is a **multi-App platform**:
each *App* (e.g. "RCA", "Intake") defines a kind of work *item*; opening an item gives
you a **workspace** — a file tree + Monaco editor + terminal + a chat panel where an
**AI agent** works alongside you (its reasoning, tool calls and messages stream live).
Each App is themed (an accent color + icon); the workspace shell is layout-driven per
App. A *profile* is a behaviour package within an App (prompt + tools + seeded files).

**A "workflow"** is a new capability: a profile can carry an **automated, multi-step
agent procedure** that runs **headlessly** (kicked off by a button or an API call)
to produce an artifact — e.g. "classify a pile of uploaded files and file them into
knowledge-base collections", or "run an analysis to a report". It reuses the same
workspace: the agent's work streams into the *same* chat you'd see in interactive use.

The defining shape of a workflow run is **produce → review → commit**: the agent
produces reviewable artifacts (safe), a **human approves at a gate**, and only then is
the irreversible action committed. A run is observable as a sequence of **phases**.

## 2. What to design (new surfaces)

All of these live **inside the existing item workspace** (they augment it; they are
not a separate app). Assume an item already open in the workspace shell.

### A. Run affordance + input preparation
- On an item whose profile **has a workflow**, surface a primary **"Run workflow"**
  action (e.g. in the workspace header/toolbar).
- Before running, the user **prepares inputs using the existing file UI** — they drop
  files into an input folder and optionally edit a small `input.json`. **Do not design
  a new upload widget**; reuse the existing file tree / editor. You may add light
  guidance (e.g. a hint of where files go, surfaced from the workflow's manifest).
- Running may take params? For v1 most workflows take none (just files). If a workflow
  declares params, a minimal "Run" confirmation could show them; keep it light.

### B. Run progress view (the centerpiece)
A view that shows a single run's live progress. Must convey **"where are we / what
broke"** at a glance.
- **Phase diagram**: the run's phases (a small ordered set, e.g. 2–5 nodes) from the
  workflow manifest, drawn as a sequence. Each phase has a **state**: `pending`,
  `running`, `passed`, `failed`, `awaiting_human`, `skipped`. The current phase is
  emphasized; a failed phase reads as an error; `awaiting_human` reads as "waiting on
  you". Within a phase, show **dynamic sub-progress** when it loops over a batch
  (e.g. "12 / 20 · 1 failed"). Updated live from an event stream.
- **Run status + meta**: overall status (`running` / `awaiting_human` / `done` /
  `error` / `cancelled`), start/elapsed time, and on completion a **result summary**
  (e.g. "processed 18 / 20" + a failures list).
- **Stop** control: cancel the run at any time (it then hands the item back to
  interactive use). Make this discoverable but not accidental.
- **Agent activity**: the agent's reasoning / tool calls / messages. **Reuse the
  existing chat rendering (`AgentEntryView`)** — the workflow streams the same events.
  Design how the phase diagram + status sit *relative to* that existing stream (e.g.
  a progress header above the chat, or a side rail).

### C. Batch / parallel runs (per-element)
- Some runs process many items in parallel (e.g. 20 files), each with its **own
  sub-log** (not one merged conversation). Design a way to show **per-element status**
  (a list: element name + state + mini progress) and to **drill into one element's**
  agent sub-stream. This is the right shape for batch; the single merged chat is for
  single-track runs.

### D. Review gate (human-in-the-loop) — `awaiting_human`
- When a run pauses at a gate, surface a prominent **decision card**: a **title**, a
  **summary to review** (could be a routing plan like "these files → these
  collections", or a generated report/markdown), and actions: **Approve**, **Reject**,
  and optionally **Revise** (free-text input). Approving commits; rejecting ends the
  run and leaves the item for the human to take over. This is the most important
  human-facing moment — make the thing-to-review legible and the choice clear.

### E. Run history (per item)
- A list of an item's past + current runs (status, when, result/failures), each opening
  its run progress view (B). An item can be run multiple times.

### F. Takeover & "fix-and-rerun" (mostly reuse)
- After a run (or stop/reject), the item is a normal workspace. The user can inspect
  and **edit/delete the run's artifact files** (they live in the file tree under
  `step_*/`) and press **Run** again to re-run only what changed. This is **reuse** of
  the existing file UI + the Run button; you mainly need to make sure the Run
  affordance and the file tree coexist cleanly.

## 3. Reuse — do NOT redesign these

- **Workspace shell** (`WorkspaceShell`): file tree + Monaco editor + terminal + chat.
  The workflow surfaces plug into it.
- **`AgentEntryView`**: the existing renderer for agent reasoning / tool cards /
  metrics. The workflow's agent stream uses it as-is.
- **App theming**: each App provides an accent color + icon (CSS `--accent` tokens);
  honor it. Field/status chips use semantic tones (danger/warn/ok/muted/accent).
- **File UI**: input prep and artifact editing happen here. No new uploader.
- **Data layer**: TanStack Query for reads; SSE for the live stream.

## 4. UX constraints

- Fit the existing visual language and density (a developer-tool / IDE-adjacent feel),
  themed per App. The workflow UI should feel like part of the workspace, not a bolt-on.
- The progress view must be **glanceable** (phase + state + "what broke") and also
  allow **drill-down** (into a phase / a batch element / the agent stream).
- Distinct, unmistakable treatment for **`awaiting_human`** (the user must notice the
  app is waiting on them) and for **`error`/`failed`** (which phase, why).
- Live updates should feel responsive (events arrive over SSE); avoid layout thrash as
  phases progress.

## 5. Out of scope / non-goals

- No visual DAG **authoring** (workflows are authored in code, not drawn). The phase
  diagram is **read-only observation**, not an editor.
- No new file-upload component; no separate "workflow admin" app.
- Mid-run free-form chat steering is not in v1 (the only mid-run human action is Stop;
  structured input happens at the review gate).

## 6. Data the UI has to work with (reference)

- **Workflow manifest** (per profile): `title`, `phases: [{ id, title }]` (the diagram
  skeleton), whether the profile has a workflow. From `GET /a/{slug}/profiles`.
- **WorkflowRun**: `status` (`pending|running|awaiting_human|done|error|cancelled`),
  `current_phase`, per-phase status/progress, `failures[]`, `result`, `started`/`ended`,
  and `pending_decision` (`{ title, summary, allow: [...] }`) while `awaiting_human`.
  From `GET /a/{slug}/items/{itemId}/runs/{runId}` and the run-list endpoint.
- **Live SSE events** (from `…/runs/{runId}/stream`): `PhaseEntered`, `StepStarted`,
  `StepPassed`, `StepFailed`, `StepSkipped`, `StepRetrying`, `AwaitingHuman`, plus the
  existing agent events (reasoning / tool calls / messages) that `AgentEntryView`
  already renders.
- **Actions**: `POST …/runs` or `POST …/items/{itemId}/run` (start); `POST
  …/runs/{runId}/cancel` (stop); `POST …/runs/{runId}/decisions` `{ choice, input? }`
  (approve / reject / revise at the gate).

## 7. Deliverables wanted from you (the designer)

1. The **run progress view** (B) — the centerpiece: phase diagram + status + how it
   composes with the existing agent chat stream. Show the key states (running,
   awaiting_human, error, done).
2. The **review decision card** (D).
3. The **batch per-element** view (C).
4. The **Run affordance** placement in the workspace header + the run **history** list.
5. Empty / loading / error states for each.
