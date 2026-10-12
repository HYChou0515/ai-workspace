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
    rule with `_artifact_path`). It holds every attempt that finished,
    rendered from the step's marked messages; an attempt cut off by
    `step_timeout_s` is cancelled before the transcript is written. Map GC prunes a departed element's transcript with
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
  - `useItemRuns` polls every 2 s while any execution is pending or running,
    every 30 s otherwise (`runsPollMs`; as amended in review round 1, below).
  - The rail entry is the only one with its name printed under the icon. The
    older five keep tooltip-only names: a 50 px rail cannot print "Search
    files", and changing them was not asked for. Its badge counts
    `awaiting_human` executions, and its accessible name says "Workflows, N
    waiting for you".
  - A row opens the execution's tab, whose path is its run id
    (`lib/executionTab.ts`; as amended by the live check, below). The
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
      another run of the same workflow on the item is executing (pending or
      running; its runs share one journal), or when the workflow no longer
      loads. Review rounds 1 and 2 added two more 409s: a run that is not the
      newest of its workflow, and one already being continued. It is refused (400) for a name that is not a
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
      Continue shows (true since review round 1, which dropped a fallback that
      offered another named step of the phase).
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
  - `show_file` takes an execution's id as `path` (`EXECUTION_PATH_PREFIX`,
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
  - The results draft names the execution (its run id) and the result
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
- **Built:**
  - `workflows.md` §1, §5.1 and §10 describe an execution as its own
    conversation that the web keeps out of the chat. Each node sees only what
    it references. The tab, Stop, Continue / Re-run, the chats about an
    execution and the waiting notice are described there. Steer is reached
    from the `⋯` menu as an aside.
  - The glossary's `human_gate` and `steering` entries are updated, and
    "execution" and "Continue from here / Re-run this step" are added.
  - `subsystems/workflow-engine.md`'s decision rows are updated, and
    `topic-hub.md` gets a note that its "workflow chat" is history.
  - The schedules overview's last run, and its note after Run now, link to
    `?open=<run id>`, the run the row shows or the one the press
    started. It used to link the schedule's conversation (`?chat=`), which the
    chat list stopped showing in P6, so that link had been landing on another
    chat.
    - The item page reads `open` once (`executionFromAddress`; an execution's
      path only, never a file) and opens that tab with the workspace
      unfolded.
  - Found while writing §10: a run launched from the web or API opens a
    conversation of its own (a schedule's runs share the schedule's one), so
    `start` lets two runs on an item go side by side, but `retry` refused while any
    run of the item was live. It now refuses only for a run of the SAME
    workflow that is executing (pending or running), the one that shares the
    journal. The live check narrowed this from "live": a run paused at a gate
    blocked Continue for as long as nobody decided.
  - Mutation probes, each reddening its test: the address check, the unfold
    on arrival, and both halves of the same-workflow guard.

## Review round 1 and the live check

Four review lenses (conformance, veracity, defect, regression) and a live check
in a real browser came back with findings. What changed, and why:

- **The execution's path is its run id.** A WorkflowRun's id already starts
  with `workflow-run:`, so the tab path was `workflow-run:workflow-run:<uuid>`
  (found live in a draft). The id the AI passes to `show_file`, the tab path
  and `?open=` are now one string. A backend test pins the id prefix
  (`test_an_executions_path_is_its_run_id`), and the FE tests use real-format
  ids, since `"r1"` doubles were what hid it.
- **Only the newest execution of a workflow owns its step files.** All of a
  workflow's runs on an item share one journal (#136). An older execution's
  Result opened the newer one's file, and its Re-run deleted the newer one's
  results.
  - `GET /runs/{id}` gives `step_files` only to the newest (`latest`).
  - `retry` refuses an older execution.
  - The tab says that a newer execution has run since, and hides Result,
    Transcript, Continue and Re-run. The execution's own messages are still
    shown, because they are per run.
  - "Newest" is by the run's creation (`ResourceMeta.created_time` from
    `search_resources`), not a listing's `RevisionInfo.created_time`, which is
    when its latest revision was written. Found by the second live check: an
    older run whose gate was decided late read as the newest. The fixture runs
    had each been written once, so they agreed with the bug.
- **`retry` is claimed by CAS before its first await.** Two presses both passed
  the checks and drove one run twice. On failure the run returns to its prior
  status.
- **The same-workflow guard counts only pending and running runs.** Found
  live: a run waiting at a gate blocked Continue for as long as nobody decided.
- **Re-run escapes the step name** in `apply_steer`'s glob, so `pick[1]`
  matches itself.
- **The transcript is written through `write_record`.** It is a record of
  effects that already happened. A quota-gated write there failed the step with
  no record, and its re-run repeated the side effects (#538).
- **Stop reaches the steerer.** It is signalled on `aside_lane(key)` as well,
  and the aside lane is forgotten in a `finally`.
- **Legacy run windows close where the run last acted** (`progress_at`), and a
  person's own messages are never dropped. A gate wait or a failed steer had
  left the window open forever, so a taken-over chat lost every later turn.
- **A retry prompt carries the previous answer**, capped at 4000 characters. A
  node reads no history (D1), so "your previous attempt" pointed at nothing.
- **FE changes:**
  - Rows that are running, waiting or failed are `aria-disabled` and cannot be
    collapsed (D7; WAI-ARIA APG Accordion).
  - A closed row shows its Result (D6).
  - A failed row links its Inputs (`inputs_path`, the run's `input.json`
    through `workflow.inputs.inputs_path`, the run's own rule; D9).
  - A failed steer proposal shows its `steer_error`.
  - `useRun` stops polling a run it cannot read.
  - The transcript check is keyed on the step's state, so an answer cached
    while the step ran does not outlive its failure.
  - Chat buttons are hidden where no chat is mounted (`canRequestChat`).
  - The view page provides the item's profile.
- **The runs list keeps a slow poll** (30 s) when nothing is executing.
  Otherwise an execution started elsewhere never reached the badge or the
  notice. A run waiting at a gate no longer counts as live for the fast pace.
- **Found live:**
  - The waiting notice is a banner in the page's flow under the top bar,
    because in the corner it covered the chat's composer.
  - "Start" and "Re-run this step" look like buttons.
  - The sidebar names an execution as the tab does (`executionName`, not
    `toLocaleString`).
  - The status bar shows no `lang:` / `UTF-8` for an execution's tab.
  - A finished execution shows no "Step N of M": a phase its path never
    needed stays not-started, and the second live check read "Done · Step 1
    of 2". The header's name block has a basis, so at 1024 px the buttons wrap
    under it instead of squeezing it to a word per line.
- **The failure notification links the execution's tab** (`execution_link`).
  The old `/a/<slug>/items/<item>` matched no page. Its title reads "A workflow
  failed".
- **D7's premise "Chat-only mode is unchanged" was wrong.** On master a
  chat-only App could start workflows from the chat's New picker and header.
  Now it has no workflow entrance at all. That is what the user asked for
  ("chat only mode不該有人在上面弄workflow"), but it is a removal, and the
  runbook says so. One consequence to weigh: a schedule's execution waiting at
  a gate cannot be decided on a chat-only App's web pages.
- **D8's "never reads step transcripts" holds for the chats the platform opens**
  (the drafts never name one), except "Ask AI why it failed", which points at
  one by design (D9). The AI's `read_file` can reach the journal like any file.

## Review round 2

Two lenses (defect + regression, veracity + conformance) on the round-1 fixes.
Each finding and the fix chosen for it:

| # | Finding | Fix |
|---|---|---|
| 1 | An older execution could still be changed (steer / confirm) or have its gate decided, which deletes or overwrites the newer one's step files; round 1 closed only Re-run. | `SupersededRun` (409 on every route, pinned over HTTP) from `steer`, `confirm_steer` (approve; reject still works) and `decide`, like `retry`. The FE hides `⋯`, the gate card, Continue and Re-run unless `/files` says `latest`. An older execution still waiting can only be stopped, and its note says so. |
| 2 | The CAS claim checked the status on one read and claimed with a later one's etag; a peer's whole claim between them was CASed over. | The status is checked on the read whose etag is claimed with; the newest / active checks come first. |
| 3 | A Stop while a Continue was being claimed (PENDING, no driver yet) answered success and was lost. | `retry` records the claim (`_claiming`); `cancel` settles a claimed run as `cancelled`, and `retry` stands down if the run is no longer its PENDING claim. A Stop from another pod in that window is not caught (the claim is pod-local). |
| 4 | One failed poll swapped the tab for an error and stopped polling for good. | `useRun` stops only for a run never read. The error view shows only without data. |
| 5 | Every 1 s poll listed the item's runs and read the workflow file. | `GET /runs/{id}` is one read again. `latest`, `step_files` and `inputs_path` are on `GET /runs/{id}/files`, which the tab asks again only when the run's status or board changes. A run waiting at a gate is polled every 5 s. |
| 6 | The banner left the page flow while its execution's tab was in front, so switching tabs moved the workspace by its height. | It kept its place there, without Open, saying "decide it below" (undone in round 3: the cited guidance says not to). |
| 7 | The Inputs link named a file the run may not have read, or one that does not exist. | `inputs_path` is the `input.json` the workflow reads NOW, for the newest only, and "" when absent. |
| 8 | Text still describing the chat flow, missing 409s, runbook WHEN / symptoms. | Reworded at HEAD (`workflows.md` schedules paragraph and Run section, `start` docstring, `WorkflowsModal`, plan P8, runbook). |

Found while fixing: FailureActions drew a different tree before and after
`/files` answered, so a click on Ask could land on a detached node. It now has
one structure, and Ask waits until the files and the transcript check are in,
so its question carries the record.

UI sources for the controls these rounds added or changed (CLAUDE.md: cite the
source in the plan and the PR):

- The count on the Workflows entry follows Material 3 "Badges" (a small count
  on a navigation icon).
- The waiting notice, as a banner in the page's flow, follows GOV.UK Design
  System "Notification banner": "something they need to know about, but that's
  not directly related to the page content" — so it is not shown for the
  execution whose tab is in front (round 3).
- Locked accordion rows follow WAI-ARIA APG Accordion: "if the accordion does
  not permit the panel to be collapsed, the header button element has
  aria-disabled set to true."

## Review round 3 and the third live check

The last round of the budget. Its fixes are small, each pinned by a test that
reddened on the code before and by a mutation probe; no mechanism was replaced.

| # | Finding | Fix |
|---|---|---|
| 1 | A superseded execution waiting at a gate still asked for a decision: the banner said "decide it below" and the badge counted it, though only Stop was left. | `GET /runs` carries `latest` per run (newest of its workflow by creation, once per list poll). The badge and the banner skip `latest: false`. |
| 2 | GOV.UK "Notification banner" is not for what is "directly relevant to the thing the user is doing on that page"; round 2 kept the banner for the execution whose tab was in front. | Back to hiding it there. The page shifts by the banner's height when that one tab comes to the front; the guidance outranks the shift. |
| 3 | A tab waiting at a gate never learned it had been superseded (the `/files` key moved only with the run), and one failed `/files` answer removed the actions for good. | The key also carries the list's `latest` for the run; a failed `/files` is asked again every 5 s. |
| 4 | A PENDING run with no driver anywhere (a pod died mid-claim, or mid-`start` in its queue) blocks every Continue of its workflow, and Stop answers success without settling it. | **Not fixed here.** Settling a PENDING run that has no local task would also settle one queued in another pod's semaphore, whose driver then starts anyway. The class predates this PR; it needs a cross-pod claim lease, which is a mechanism. Recorded as a follow-up. |
| 5 | The steer confirm card still showed Approve on an older execution (answered 409), and the query keys' docs were misplaced. | The card shows only for the newest; the docs moved. |

The third live check found the badge trailing an Approve by up to 30 s: a gate
wait is the slow pace, and deciding refreshed only the one run. Deciding,
proposing and confirming a change now refresh the item's list too
(`useDecide` / `useSteerRun` / `useConfirmSteer`).

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
- **Done** (2026-10-12; branch `ffad33cd` plus the phase fix below, base
  `5aa45d3b`). This was the real app in a real Chromium, with one stand-in: the
  model, a runner that logs each turn's prompt and the history it was handed.
  The same flow ran on both through the HTTP API: a person chats, a workflow
  starts (first trying to take over that chat), the person chats again while it
  waits at its gate, a change is proposed, and a failing execution is started.
  | Check | Base `5aa45d3b` | Branch |
  |---|---|---|
  | `POST /run?chat_id=<that chat>` | 202, the chat is taken over | 400 |
  | The node's history | the chat's "my private note" and its reply | empty |
  | The chat's next turn | includes the node's answer | only the chat's own two messages |
  | The steer's JSON | persisted in the conversation and shown in it | in no conversation, not on the page: the card shows the instruction |
  | Starting a workflow | from the chat | from the sidebar's Start: a tab opens, and the chat count stays 1 → 1 |
  | A gate | a card inside the chat | the badge "1 waiting for you"; the banner shows while a file is in front |
  | A failing step | progress bar in the chat | Continue from here + Re-run this step |
  | Layout | — | no horizontal overflow, no clipped row, no page error, at 1440 and 1024 |
  - Found by it: a phase satisfied from the journal (its step skipped because
    an earlier run did it) counted `done` but stayed `pending`, so the tab read
    "Not started" next to its Result. A skipped step now marks a still-pending
    phase `skipped` ("Already done"). This predates the PR; the old diagram had
    the same blind spot.
