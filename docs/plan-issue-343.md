# Plan — #343 Launch a workflow *in the current chat* (takeover)

> Status: grill-locked, building via `/tdd` (flat phases, one commit per phase).

## Problem

Today a workflow is launched via `POST /a/{slug}/items/{item_id}/run`, which **always
creates a fresh `Conversation`** (a workflow chat) and streams the run there. So a user
who prepares the ground in a chat — uploading files, discussing with the agent, letting
the agent produce artifacts — is then dumped into an **empty new workflow chat** that
does not carry any of that preparation.

#343: *"user 先用 chat 把該有的準備好,接著再 chat 裡面啟動 workflow."* — let the user
prepare in a chat and then launch the workflow **in that same chat**, carrying the
prepared context and files.

## Principle

An **App is an instance of one template**; behaviour is gated by `app.json`
config/capability, **never** by app slug. This feature is **platform-general** — it works
for every App. (The FE chat surface was already unified onto `ItemChatShell` for every App
in #200, with no `slug === "…"` fork, so this is a natural fit.)

## Locked design

| Decision | Resolution |
|---|---|
| Where the workflow runs | **Takeover the current conversation** — set `run_id` on the existing `Conversation` instead of creating a new one. |
| How preparation reaches the workflow | Agent nodes **auto-inherit the chat history** (windowed: 40 msgs / 24K tokens, via `WorkflowExecutor.drive_turn` → `history_messages=conv.messages`) + workspace files already staged. `input.json` stays **optional** (`{}` fallback). No extra `inputs["context"]` fold (it would double-inject with the inherited history). |
| Who triggers it | **The user**, via a menu in the **current chat header** listing the profile's workflows. No new agent tool. |
| Confirmation | Reuse the existing `WorkflowLaunchDialog` (phase preview + `can_run` preflight). |
| Lifecycle | **Episode.** After the run reaches a terminal state, `run_id` stays (marks which run ran + keeps the done progress bar), the composer keeps working (free chatting continues), and the user may launch **another** workflow in the **same thread** (`run_id` run1 → run2). |
| The existing "+ New → new workflow chat" entry | Kept — headless / fresh launches still need it; the two entries coexist. |
| In-flight free turn at launch time | Not blocked; the workflow's first agent turn simply FIFO-queues behind it (existing engine behaviour). |

### Verified facts driving the plan

- `orchestrator.start` **skips the one-active-run-per-item guard when `chat_id` is given**
  (runs are per-chat), but there is **no per-chat guard** — takeover must add
  "this chat already has an active run → 409".
- `locator.engine_key`: the **default chat** (earliest-born free chat, `run_id is None`)
  keys on `item_id`; every other chat keys on its own id. Once we set `run_id` on the
  default chat it is no longer "free", so `find_default_conversation` returns a
  different/None chat and `engine_key(item, that_chat)` falls back to the chat's own id —
  which **matches** the run key (`chat_id or item_id` == `chat_id`), so the SSE stream
  lines up. BUT item-level legacy endpoints + file-change broadcasts (keyed on `item_id`)
  lose their default home → **P2** must preserve an interactive default.
- `send_chat_message` (`chat_routes.py`) does **not** gate on `run_id`, so free chatting
  in a chat whose run is terminal already works — "keep chatting after" is essentially free.

## Phases (flat integers; one commit each)

### P1 — Backend takeover
`POST /a/{slug}/items/{item_id}/run` accepts an optional `chat_id`:
- With `chat_id`: **reuse** that Conversation (validate it belongs to the item) and set its
  `run_id` to the new run, instead of creating a new Conversation.
- Add a **per-chat active-run guard**: if the target chat already has an active run
  (`PENDING`/`RUNNING`/`AWAITING_HUMAN`) → `409`.
- Allow relaunch on a chat whose previous run is **terminal** (`run_id` run1 → run2).
- Without `chat_id`: unchanged (creates a new workflow chat).

Tests: takeover sets run_id on the existing conv (no new conv); 409 when the chat has an
active run; relaunch after terminal updates run_id; legacy no-`chat_id` path unchanged.

### P2 — Backend default-chat / stream integrity
- When the taken-over chat was the item's **default free chat**, preserve an interactive
  default so item-level legacy endpoints + file-change broadcasts keep a home (materialize
  a fresh empty free chat, or otherwise guarantee `find_default_conversation` still
  resolves).
- Guard `_prune_runs` so it never deletes a run still referenced by a live chat's `run_id`.

Tests: after taking over the default chat, item-level send/stream still resolve a default;
prune keeps a run referenced by a chat.

### P3 — FE launch entry (current chat header)
- A "▷ 啟動 workflow" menu in the current chat header, listing the profile's workflows,
  shown **only when the chat has no active run**.
- Picking a workflow opens `WorkflowLaunchDialog` in a **takeover** mode → on confirm calls
  `run` with the current `chat_id` → the current chat shows `WorkflowProgress` and streams
  the run.

Tests (vitest): menu lists workflows + hidden during an active run; confirm calls the
takeover run with `chat_id`; the current chat renders the progress bar afterwards.

### P4 — FE same-thread relaunch
- After a run reaches terminal, the launch menu reappears; `WorkflowProgress`/`useRun`
  follow the **new** `run_id`; composer and relaunch coexist.

Tests (vitest): terminal → menu returns; relaunch swaps to the new run's progress.

### P5 — Live check + full gate
- Live check (vs local Ollama): prepare in a chat (upload + discuss) → launch the workflow
  in that chat → verify it inherits the context, hits `human_gate`, and can be relaunched
  in the same thread.
- Full backend 100% coverage gate + `ruff` + `ty` (whole project) + FE `typecheck` +
  `build` + `vitest`.

## Out of scope / deferred
- Folding chat text into `inputs["context"]` for deterministic nodes (agent nodes already
  inherit history).
- An agent-callable `run_workflow` tool (user-initiated only).
