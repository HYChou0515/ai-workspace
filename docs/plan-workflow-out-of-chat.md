# Plan — workflows out of the chat: each node sees only what it references

> Status: grill-locked 2026-10-12 (D1–D10 below, each the user's answer to one
> question); build via `/tdd`, flat phases, one commit each.

## Problem

Two reports, both from running a workflow in an item's chat:

1. **Context leaks both ways.** The workflow's turns and the chat's turns read
   each other.
2. **Typing in a workflow chat produces odd JSON**, not a conversation.

Read from `origin/master` at `5aa45d3b` (not measured live):

- **One thread, no marks.** A workflow chat is a `Conversation` with `run_id`
  set (`resources/conversation.py:278`). `Message` carries no run or step
  field, and an agent step appends its turn to that thread
  (`api/workflow_exec.py:235-245`, `persist`). The step's prompt is not
  persisted, so the thread holds answers without their questions.
- **Every agent node reads the whole thread.** `drive_turn` builds the turn
  with `history_messages=conv.messages` (`api/workflow_exec.py:215`): the
  chat before a takeover, every earlier step, earlier runs in a reused
  schedule chat, and steer replies. Map elements and the steerer go through
  the same `drive_turn`, so siblings read each other too.
- **The chat reads the run.** A chat turn's history is everything before the
  user's message (`api/chat_send.py:1364`, `_history_before`). `history_items`
  filters by role and size only, never by origin.
- **Both were designed in.** #343 locked "agent nodes auto-inherit the chat
  history" and takeover via `?chat_id=` (`plan-issue-343.md`).
  `workflows.md` §1 / §5.1 make a run and the interactive workspace "two modes
  of one item sharing one conversation", for human takeover (§10).
- **The JSON is the steerer's.** The FE routes every message typed in a chat
  with a `run_id` to steer, whatever the run's status
  (`web/src/components/ItemChatShell.tsx:643`). `propose_steer` wraps the text
  in a prompt that ends "Reply with ONLY the JSON object" and runs it as a
  normal turn (`workflow/steer.py:143-149`, `:173`), so the raw JSON is
  persisted and shown as the agent's reply, up to three times on parse
  retries. The user's own words are never shown. #343 had locked the opposite
  (after a run ends, "free chatting continues").

## Decisions (grilled 2026-10-12)

| # | Decision | Overturns |
|---|---|---|
| D1 | An agent node never reads chat history. What a user prepares reaches a workflow through workspace files and `input.json`. | #343 "agent nodes auto-inherit the chat history" |
| D2 | Steer stays, as a deliberate action in an execution's `⋯` menu ("rarely needed, so it may take a click more"). Only the confirm card shows, with the user's own request on it. The steerer's JSON is neither persisted nor shown. The chat composer never steers. | #288 "free text in a run's chat" |
| D3 | Workflows leave the chat. An execution has its own page; a chat is never taken over. | #343 takeover; `workflows.md` §1 / §5.1 / §10 "one conversation for both" |
| D4 | One resource, two surfaces. An execution's transcript stays a `Conversation` (with `run_id`); only the web separates them. No data migration. | — |
| D5 | A node sees only what it references: upstream `outputs` it names, files, `input.json`. Two nodes with no reference between them never affect each other. This covers parallel branches and map elements. | — |
| D6 | The execution page lists one row per step, collapsed, with its status and `outputs`. Expanding a row shows the step's prompt, reasoning, tool calls and reply. The step's prompt is now persisted. A gate is decided in its row. A running step is expanded. | — |
| D7 | Workspace mode gets a **labelled** activity-bar entry (NN/g: an icon needs a visible label). It carries a badge with the count of executions awaiting a gate, plus a non-blocking notice naming which execution waits and for what. An execution opens as an editor-area tab, with Stop and "Step N of M". Rows support Expand all / Collapse all; a running, waiting or failed step is never collapsed. Chat-only mode is unchanged and has no workflows. | the chat header's launch menu |
| D8 | The chat's AI never reads step transcripts, but may read an execution's result files. It can summon an execution card with `show_file`. An execution page has "Open a chat with these results". The AI cannot start or change an execution. | — |
| D9 | A failed step is expanded. It shows its error, attempts and inputs (as file links), plus **Continue from here** (resume: done steps skip) and **Re-run this step** (invalidate it; downstream cascades). **Ask AI why it failed** opens a chat pointing at the step's journal. Each agent step writes its transcript to the journal (`step_<name>/<key>/transcript.md`) so the AI reads it as a file. | — |
| D10 | The UI calls it **Workflows**. An execution is named by workflow and start time ("Classify wafers · 10/12 14:03"); buttons are verbs. Code, API and glossary keep `run`. | the `Run` / `Runs` noun-and-verb clash |

Chosen while planning (the user may overturn):

- `POST /run?chat_id=` answers **400** naming the change. It does not silently
  ignore the parameter. Callers are only the FE takeover menu and
  `scripts/check_inchat_workflow_343.py`; #197's headless launch never passes
  it.
- Every other entrance keeps working and opens a fresh execution, which the
  Workflows sidebar lists. The entrances are:
  - `WorkflowsModal` (`web/src/components/WorkflowsModal.tsx:103`);
  - `useWorkflow` (`web/src/hooks/useWorkflow.ts:70`);
  - a WUI page through its bridge (`web/src/renderers/wui/bridge.ts:248`);
  - schedules;
  - #197's headless launch.

  Only `ItemChatShell.tsx:304` passes a `chatId` (the takeover).
- A free chat never holds an execution. A legacy chat taken over under #343
  keeps its own messages, and its chat turns exclude that run's step messages.
  Messages gain a run mark for this; legacy ones are matched by the run's time
  window.
- A schedule keeps reusing one conversation across its executions, as
  `locator.chat_for_schedule` does on purpose:
  - the one-run-per-chat rule is what stops a slow workflow on a fast schedule
    from piling up executions;
  - a chat per firing is 1440 conversations a day at `every: minutes`.

  The execution tab shows one execution's messages by their run mark (P3), and
  P2 already keeps executions from reading each other. Schedule overview and
  outbound notification links open the execution tab.

## Phases

**P1 — audit what shipped.** Walk every App's packaged and sample workflows for
a node whose prompt relies on an earlier node's conversation (e.g. "as above",
or no reference to the data it uses). Convert each one to an explicit reference.
Report the list before P2 changes behaviour.
- **Built:** nothing to convert. Every agent node names its data, read on
  `b7b83a94`:
  - topic-hub `collections` (classify: `Read the file {f}`);
  - topic-hub `consolidate` (`MEMORY.md` and the notes; recent chats arrive as
    the explicit `inputs["context"]`);
  - topic-hub `memory` (digest per file, then `refresh_index` from the notes
    list);
  - `sample-workflows/image-to-knowledge` (`{image}`, then `notes/{image}.md`);
  - playground DSL `file-uploads` (`Read {file}`);
  - the `workflow new` recipes, which read no input.

  Playground `alpha` / `beta` have no agent node.

**P2 — a node's context is its references.**
- Agent nodes start from an empty history: no `conv.messages`. This covers map
  elements, parallel branches and the steerer.
- The step's prompt is persisted with its turn.
- Each agent step writes `transcript.md` into its journal key.

**P3 — the chat never reads a run.**
- `Message` gains the run mark.
- `history_items` callers for chat turns drop messages of any run. Legacy mixed
  chats are matched by the run window.

**P4 — no takeover.**
- `?chat_id=` returns 400 with a reason.
- Runbook entry in `docs/migrations.md`.

The execution tab (P8) selects an execution's messages by the run mark from P3,
so a schedule's shared conversation shows one execution at a time.

**P5 — steer off the thread.**
- The steerer's turn persists nothing to the execution's conversation.
- `pending_steer` carries the instruction for the confirm card.

**P6 — chat surfaces stop showing runs.**
- The chat list, switcher and manage-chats leave out conversations with
  `run_id`.
- The chat header's launch menu and composer steer routing go.

**P7 — the Workflows sidebar.**
- A labelled activity-bar entry, with a badge of executions awaiting a gate.
- The sidebar lists the workflows to start, then executions newest first
  (name · time, status, Step N of M, schedule mark).

**P8 — the execution tab.**
- Step rows per D6 / D7, with Expand all / Collapse all.
- A gate is decided inline.
- Stop.
- A failed step offers Continue from here and Re-run this step.
- `⋯` → Change this execution… (steer; D2).

**P9 — the waiting notice.** Wherever the user is in the workspace, a
non-blocking notice names the execution and what it waits for. It opens that
gate.

**P10 — the AI side.**
- A `show_file` execution card.
- "Open a chat with these results".
- "Ask AI why it failed", reading `transcript.md`.

**P11 — docs.**
- Rewrite `workflows.md` §1 / §5.1 / §10.
- Update the glossary (steering, human_gate rendering).
- Point the schedule overview at the execution tab.

## Verification

- Targeted tests, the four gates, and typecheck per phase.
- Each isolation claim (D1, D5, P3) gets a test that reddens on today's code:
  a node or chat turn whose built history contains a message it must not see.
  The line that applies each filter is mutation-probed.
- **Live check** (real browser, workspace mode), against a base differential
  on `5aa45d3b`:
  - Start a workflow from the sidebar; it opens a tab and leaves the chat
    alone.
  - Chat during it; the execution's steps do not appear in the chat's
    context, and the chat's messages do not appear in a node's context.
  - A gate badges and notifies.
  - A failing step offers Continue / Re-run.
  - Change this execution shows the card, not JSON.
  - Measure layouts at two widths.
