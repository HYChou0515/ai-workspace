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
| D9 | A failed step is expanded. It shows its error, attempts and inputs (as file links), plus **Continue from here** (resume: done steps skip) and **Re-run this step** (invalidate it; downstream cascades). **Ask AI why it failed** opens a chat pointing at the step's journal. Each agent step writes its transcript to the journal, beside its artifact (`step_<name>/<key or main>.transcript.md`), so the AI reads it as a file. | — |
| D10 | The UI calls it **Workflows**. An execution is named by workflow and start time ("Classify wafers · 10/12 14:03"); buttons are verbs. Code, API and glossary keep `run`. | the `Run` / `Runs` noun-and-verb clash |

Chosen while planning (the user may overturn):

- `POST /run?chat_id=` answers **400** naming the change. It does not silently
  ignore the parameter. Callers were only the FE takeover menu and
  `scripts/check_inchat_workflow_343.py`; #197's headless launch never passes
  it. The script is removed with the takeover (P4).
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
- **Built:**
  - `drive_turn` hands every node an empty history, so a node reads none of
    the thread it persists to. Map elements and the steerer drive through the
    same call. The #624 "history reduced" notice on workflow turns went with it:
    an empty history is never reduced. Its test, whose premise this overturns,
    was replaced by `test_a_node_reads_nothing_of_the_conversation_it_runs_on`.
  - The run mark moved here from P3, because persisting the prompt needs it.
    `Message` gains `run_id` and `step` (`name`, or `name/key` for one loop
    element). Each turn persists its prompt first (`role="user"`,
    `driven_by="workflow"`, so off-hours autonomy does not hear a person),
    then its turn, all marked.
  - The step reaches the executor through `steps.CURRENT_STEP`, a ContextVar
    `agent_step` / `agent_write_step` set around their turn. It is not a
    `DriveTurn` argument, which would change 48 test doubles to pass a mark.
    Steps side by side (`gather`, map elements) each hold their own copy.
  - The transcript sits beside the artifact,
    `step_<name>/<key or main>.transcript.md` (`engine.transcript_path`, one
    rule with `_artifact_path`). It holds every attempt, rendered from the
    step's marked messages. Map GC prunes a departed element's transcript with
    its artifact; steer's `step_<name>/*` invalidation already took both.
  - Mutation-probed: 13 guard lines, each reddening the P2 tests; the control
    stays green.

**P3 — the chat never reads a run.**
- `Message` gains the run mark (done in P2).
- `history_items` callers for chat turns drop messages of any run. Legacy mixed
  chats are matched by the run window.
- **Built:**
  - An app chat builds its history in one place (`chat_send.py`, the only
    `history_messages=` a chat passes), now through
    `run_windows.without_runs`. That drops every message a run marked. In a chat
    #343 took over (`conv.run_id` set), it also drops the unmarked messages
    inside the window of each run that drove that chat (`WorkflowRun` by
    `item_id`, then `chat_id`). A message with no time cannot be placed and is
    kept. A free chat never pays for the lookup.
  - Pinned through `POST …/chats/{id}/messages`. Mutation-probed: 7 guard
    lines each redden; the control stays green. One probe first came back
    unpinned (counting another chat's run), and a test now covers it.

**P4 — no takeover.**
- `?chat_id=` returns 400 with a reason.
- Runbook entry in `docs/migrations.md`.
- **Built:**
  - The route refuses `chat_id` before anything is staged, the way it refuses
    an upload that escapes the workspace: nothing is written and no run
    starts.
  - The takeover branch is gone, so every launch opens its own conversation.
    The cleanup no longer asks whether the chat was the caller's (`ours`): it
    always is the route's.
  - The orchestrator's one-run-per-chat guard stays, because a schedule's
    shared conversation relies on it.
  - Five #343 takeover tests were replaced by one: a launch naming a chat is
    refused and changes nothing. `scripts/check_inchat_workflow_343.py` is
    removed.
  - Runbook: `#pr-913`, started here and extended by later phases.

The execution tab (P8) selects an execution's messages by the run mark from P3,
so a schedule's shared conversation shows one execution at a time.

**P5 — steer off the thread.**
- The steerer's turn persists nothing to the execution's conversation.
- `pending_steer` carries the instruction for the confirm card.
- **Built:**
  - `WorkflowHandle.aside_turn` is a turn that is not part of the run's record.
    The executor wires it as `drive_turn(record=False)`, which persists
    nothing, writes no transcript, and runs on a lane of its own
    (`<chat>#aside`, dropped after), so nothing it streams reaches the run's
    live stream.
  - `propose_steer` drives through it, falling back to `drive_turn` only where
    nothing wired it (unit tests).
  - `SteerPlan.instruction` already carried the person's request, so the card
    (P8) reads it. Nothing new was needed there.
  - Pinned through `wire_handle` and over `POST …/steer`. Mutation-probed: 4
    guard lines each redden.

**P6 — chat surfaces stop showing runs.**
- The chat list, switcher and manage-chats leave out conversations with
  `run_id`.
- The chat header's launch menu and composer steer routing go.
- **Built:**
  - `GET …/chats` lists a conversation with `run_id` only if a person spoke in
    it (`chat_info.is_chat`: a `user` message no driver sent and no run
    marked). That is a chat #343 took over, or one where a person chatted on
    after a run ended. A run's own conversation, opened by its launch or a
    schedule's, holds no such message and is left out.
  - A first cut asked for a person's message *before the first run started*.
    That would have hidden a #343 chat in which the person spoke only after its
    run, and since the execution tab shows a run's messages only, their words
    would have been shown nowhere.
  - The chat no longer carries any workflow surface: the bar's
    run-in-this-chat menu (`WorkflowLaunchMenu`, deleted), the New picker's
    workflows and their launch dialog, the progress bar, the gate and steer
    cards, the schedules banner (`scheduleChat.*`), the composer's steer
    route, and the header's **Workflows** button.
  - Chosen here (may be overturned):
    - The **Workflows panel** (`WorkflowsModal`, #323: run, download, import,
      templates, schedule times) is more than a launcher. It moves with
      launching to the Workflows sidebar (P7) rather than being removed.
    - The **New picker** became a one-click "New chat" button. Without
      workflows it was a menu of one item, and #466's ellipsis meant "pick
      next".
    - The chat switcher's and manage-chats' run **status badge** stays, for the
      legacy #343 chats still listed.
  - Mutation-probed: the list's rule, 6 guard lines; re-adding the steer
    route, the bar, or the ellipsis each redden.

**P7 — the Workflows sidebar.**
- A labelled activity-bar entry, with a badge of executions awaiting a gate.
- The sidebar lists the workflows to start, then executions newest first
  (name · time, status, Step N of M, schedule mark).
- **Built:**
  - `WorkflowsSidebar` (`web/src/components/`) lists what `useLaunchableWorkflows`
    returns: the profile's workflows plus the workspace's own, with the same-id
    shadowing and broken-file skip the New picker used. Start goes through the
    pre-flight dialog. A failed start says why (`role="alert"`). "Manage
    workflows…" opens the Workflows panel, whose Run now opens the tab too
    (`onRun`).
  - Executions are sorted by `started`. Each row shows its status in words
    (Starting / Running / Waiting for you / Done / Failed / Stopped) and
    `stepOf(phaseView(...))` as "Step N of M". It is marked "Scheduled" when
    the run carries `schedule_key`, a new `WorkflowRun` field. That field is
    set only by `start_page_schedule`, so runs started before this version are
    never marked.
  - `useItemRuns` polls every 2 s while any execution is not terminal
    (`runsPollMs`), so progress and the badge move on their own.
  - The rail entry is the only one with its name printed under the icon. The
    older five keep tooltip-only names: a 50 px rail cannot print "Search
    files", and changing them was not asked for. Its badge counts
    `awaiting_human` executions, and its accessible name says "Workflows, N
    waiting for you".
  - A row opens `workflow-run:<run_id>` (`lib/executionTab.ts`) as a tab. The
    tab renders from P8.
  - Mutation probes, each reddening its test: the waiting filter, the profile
    fallback, the printed label, the sort order, the schedule mark, and the
    failure text.

**P8 — the execution tab.**
- Step rows per D6 / D7, with Expand all / Collapse all.
- A gate is decided inline.
- Stop.
- A failed step offers Continue from here and Re-run this step.
- `⋯` → Change this execution… (steer; D2).
- **Built:**
  - Backend. `POST /runs/{id}/retry` (`Orchestrator.retry`) drives a failed or
    stopped execution again, as the same run.
    - `rerun: ""` is Continue from here: what passed skips.
    - `rerun: <step>` is Re-run this step. Its artifacts are dropped first through
      `apply_steer`, which also writes the audit receipt. The steps after it
      re-run as far as their inputs changed.
    - It is refused (409) for a run that is not `error` / `cancelled`, while
      another run of the item is live (the journal is shared), or when the
      workflow no longer loads. It is refused (400) for a name that is not a
      named step on the board; the name reaches a journal glob.
    - The phases, the board, `ended` and `result` restart: a skipped step counts
      done again on the way back.
    - The presser's identity is kept, as a gate decision keeps it.
  - `GET /runs/{id}` carries `step_files`, each step's result and transcript
    paths from the journal's own layout (`journal_stem`, `journal_dir_of`).
  - Found while building: `GET /runs/{id}` and `/runs/{id}/stream` never
    checked that the run belongs to the URL's item, so read access to one item
    read every item's runs and live streams. Both now 404 for another item's
    run.
  - A step's messages carry their phase (`Message.phase`, set from
    `StepRef.phase`). Rows are phases ("Step N of M" counts them), and a loop
    element leaves the board once it ends.
  - FE. `ExecutionView` is the tab body: the `execution` renderer entry for
    `workflow-run:` paths, with the item's profile from `ItemProfileProvider`.
    - The header holds the name (`executionName`), the status, Step N of M,
      Stop while live, Continue when stopped, and `⋯` → Change this
      execution…. That opens a box whose proposal shows as the
      `SteerConfirmCard`.
    - The rows are an accordion (NN/g "Accordions on Desktop"; WAI-ARIA APG
      Accordion: a heading button with `aria-expanded` / `aria-controls`). A
      running, waiting or failed row opens on arrival, and Collapse all leaves
      it open.
    - An open row shows its messages (`run_id` and `phase` match), its steps'
      attempts and error, and its Result and Transcript links. Transcript is
      offered only when the file exists; only agent steps write one. A gate is
      decided in its row (`WorkflowDecisionCard` + `CardDiffReview`).
    - A failed row offers Continue from here and Re-run this step. Re-run
      names the failed named step; a failed loop element has none, so only
      Continue shows.
    - Messages with no phase, and unmarked ones in a conversation that is this
      execution's own, show under "Not in a step"; they are never guessed into
      a row.
  - The first running row also shows what the execution's own lane has
    streamed beyond the saved thread (`useItemChat` minus the persisted
    entries). A map element's turn runs on a lane of its own, which the run's
    stream does not carry, so it shows only once saved.
  - In the shell, an execution tab is `present` to the auto-close, is kept out
    of Recently opened (ten slots), and is labelled by `ExecutionTitle` in the
    tab and the breadcrumb.
  - Mutation probes, each reddening its test:
    - the retry guards: status, unknown step, live run, invalidation, the
      counter reset and the `ended` / `result` reset;
    - the item checks on get and stream, and the `step_files` path;
    - the run filter;
    - the sticky rule and Collapse all;
    - the live tail;
    - the rerun name;
    - Stop while live;
    - the gate card;
    - the failure text;
    - the transcript check;
    - the presence exemption, the recent-files guard, the tab label and the
      renderer entry.

**P9 — the waiting notice.** Wherever the user is in the workspace, a
non-blocking notice names the execution and what it waits for. It opens that
gate.

- **Built:** `WaitingNotice` is mounted by the shell in workspace mode, folded
  IDE or not. Open unfolds the IDE and opens that execution's tab.
  - It uses the poll the sidebar already makes (`useItemRuns`).
  - It speaks of the newest execution that waits at a gate or on a proposed
    change, says the gate's title (or "A change to confirm"), and counts the
    others, leaving out the one in front.
  - It is not shown while that execution's tab is in front. Dismiss holds for
    that one wait (run plus gate phase, or plus a pending steer), so the next
    gate asks again.
  - It is non-modal, with `role="status"`, and no timer takes it away, because
    it asks for an action (NN/g, "Indicators, Validations, and Notifications").
  - Mutation probes, each reddening its test: the waiting filter, the
    dismissal, the dismissal key, the in-front exclusion, the newest-first
    sort, the count, and the shell's active path.

**P10 — the AI side.**
- A `show_file` execution card.
- "Open a chat with these results".
- "Ask AI why it failed", reading `transcript.md`.
- **Built:**
  - `show_file` takes `path: "workflow-run:<id>"` (`EXECUTION_PATH_PREFIX`,
    pinned equal to the FE's `PREFIX` by a test). It declares a card with mime
    `application/x-workflow-execution` for an execution of this workspace, and
    declares nothing for another workspace's or an unknown one.
  - The chat draws that card as `ExecutionCard`: the execution's name (the
    shared `ExecutionTitle`, its own module so the chat does not import the
    execution view) and the caption, with no path or byte count. It opens the
    tab when the workspace is on screen, and otherwise links to the item's
    editor-area page.
  - The tab's "Open a chat with these results" (on an ended execution) and a
    failed row's "Ask AI why it failed" publish on `chatRequestBus`.
    - `ItemChatShell` opens a NEW chat. The execution's conversation is never
      continued (D3).
    - It hands the text to that chat's composer as a draft, never a send,
      through `agentDraftBus`. The hand-off happens in an effect after the new
      chat is drawn; from the create's callback the draft went to the chat in
      front before (probed).
    - `WorkspaceShell` unfolds a folded chat.
  - The results draft names the execution (`workflow-run:<id>`) and the result
    files of the steps that passed or were skipped, never transcripts (D8). The
    why draft names the step, its error, and its transcript when one exists.
  - How the AI learns an id: these drafts. A chat started anywhere else does
    not know the item's executions.
  - Mutation probes, each reddening its test:
    - the item check in `show_file`, the card's routing, and the chat
      unfolding;
    - the terminal-only results button, and the results filter;
    - the why draft's transcript and error;
    - the hand-off, and the callback ordering.

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
