# Plan — Issue #132: multi-chat UX (chat 一多會亂掉)

> Status: locked via `/grill-me` (2026-06-23). Build via `/tdd`.

## Problem

The topic-hub per-item multi-chat shell (`ItemChatShell`) renders every chat as a
**horizontal tab rail** (`ItemChatList`) that `flex-wrap`s into several rows once a
few chats exist, plus two separate launch controls (`+ New chat`, red `Run
workflow`). Workflow runs and free chats are mixed in the same rail, distinguished
only by label text + a `· run` suffix. Free chats with no title all collapse to
`Chat` / `Free chat`, so they are indistinguishable. Issue #132's three asks:

1. **chat history 要有獨立地方讓人選** — a dedicated place to browse/select chats.
2. **chat 命名** — chats need names.
3. **workflow 區分 chat** — tell workflow runs apart from free chats.

## Locked decisions

| # | Decision | Detail |
|---|----------|--------|
| 1 | **List surface** | A top-bar **switcher dropdown** (quick switch) + a **manage modal** (large view of all chats: rename / delete / time). |
| 2 | **Workflow vs chat** | **One unified list, no grouping.** Workflow rows get a `⚙` icon + a status badge (`●` running / `⏸` awaiting / `✓` done). A run is still a readable conversation — decoration, not a separate panel. |
| 3 | **Naming** | Free chat default display name = **first user message snippet** (instant, zero LLM, **display-only**). Empty chat → `新對話`. Workflow chat keeps its manifest title. Manual rename lives in the modal. |
| 4 | **Delete** | **Delete also cancels the run** (with a confirm dialog) for a running workflow chat. Free / finished chats delete directly. Deleting the last chat → a fresh one is auto-opened. |
| 5 | **New-item entry** | Merge `+ New chat` and `Run workflow` into **one `+ 新增` picker**: `Free chat` + the seed profile's N workflows (matches `topic-hub.md` §4). |
| 6 | **Ordering** | **Most-recent-activity first** (`updated_time` desc). **No "main/default chat" concept in the UX** — drop the `is_default` privilege from labels/sort. The backend default-chat resolver stays only as the RCA / legacy-data fallback (topic-hub FE never hits it). |
| 7 | **Modal row actions** | Each row has **explicit buttons** — switch / edit (inline rename) / delete (confirm). No click-whole-row-to-switch. |
| + | **Modal search** | The modal has a **search/filter box** (keeps "many chats" navigable). |

### Out of scope / untouched
- `ItemChatPanel` (active chat = `AgentPanel` + phase bar + `human_gate` Continue) — unchanged.
- `WorkspaceShell` width / `chatMaximized` logic — unchanged.
- RCA's single `AgentPanel` — unchanged (redesign lives entirely in the topic-hub shell).
- Backend `default chat` resolver (`api/chats.py`) — kept for RCA / legacy item-level
  (no-`chat_id`) endpoints; only the topic-hub list **sort + label** stop privileging it.

## Backend phases

### P1 — Enrich the chat-list summary + reorder
`_ChatInfo` (`api/app.py:236`) gains:
- `status: str | None` — `WorkflowRun.status` joined via `conv.run_id` (None for free chats).
- `last_activity_ms: int | None` — specstar revision `updated_time` (ms) for the conversation.
- `name_hint: str` — first user message, truncated server-side (e.g. ≤ 60 chars); `""` when no user message yet.

`list_chats` (`api/app.py:2004`): sort by `last_activity_ms` **desc** (fallback
`created_ms`, then `chat_id`); **remove** the `not is_default` primary sort key.
`is_default` stays on the payload (RCA may read it) but no longer drives order.
Joining status: one `WorkflowRun` RM lookup per workflow chat in the item (bounded
per-item set) — or a single `item_id` query → dict keyed by `run_id`.

Surface `updated_time` from the revision: extend `list_item_conversations`
(`api/chats.py`) to also yield `r.info.updated_time`, or read `r.info` in the route.

### P2 — Delete a chat
`DELETE /a/{slug}/items/{item_id}/chats/{chat_id}`:
1. If `conv.run_id` and the run is non-terminal → `await workflow_orchestrator.cancel(run_id, item_id)`.
2. `turn_engine.forget(_engine_key(item_id, chat_id))` (drop any in-flight turn / SSE).
3. `conv_rm.delete(chat_id)`.
Idempotent-ish: 404 on unknown chat. The confirm dialog lives in the FE.

### P3 — Rename a chat
`PATCH /a/{slug}/items/{item_id}/chats/{chat_id}` with `{title}` → set
`conv.title` and `conv_rm.update`. Returns the updated `_ChatInfo`.

## Frontend phases (vitest TDD)

### P4 — `ChatSwitcher` (replaces the horizontal `ItemChatList`)
Top-bar dropdown: trigger shows the active chat's display name; menu lists every
chat (name, `⚙`+status badge for workflow chats, relative activity time), most-recent
first; a footer item `管理所有 chat…` opens the manage modal. Parent still owns selection.

### P5 — `NewItemPicker` (merge the two launchers)
One `+ 新增` menu: `💬 Free chat` + a divider + the profile's workflows (`⚙` + title +
description). `onFreeChat` / `onWorkflow(workflowId)` callbacks (reuse `ItemChatShell`'s
existing `createFreeChat` / `startRun` handlers).

### P6 — `ManageChatsModal`
A modal table over all chats: columns 名稱 / 類型·狀態 / 最近活躍 / 訊息數, plus a
**search box** filtering by name. Per-row buttons: **切換**(select + close) /
**編輯**(inline rename → PATCH) / **刪除**(confirm → DELETE, "delete also cancels a
running workflow"). Column-header sort optional; default sort = activity desc.

### P7 — Wiring + labels
- `chatLabel`: `title || name_hint || "新對話"`; remove the `is_default ? "Chat"` special case.
- New TanStack mutations: `renameChat` (PATCH) + `deleteChat` (DELETE) in `useItemChats`,
  each `invalidateQueries(qk.itemChats(...))`. After delete, if the active chat was
  removed, select the new top-of-list (or auto-open if empty).
- `itemChatApi` (`web/src/api/itemChats.ts`) gains `renameChat` / `deleteChat`;
  `ItemChatSummary` mirrors the new `status` / `last_activity_ms` / `name_hint` fields.
- `ItemChatShell` top bar: `<ChatSwitcher/>` + `<NewItemPicker/>`; mount `<ManageChatsModal/>`.

## Definition of Done
- Each backend route + behaviour change driven red→green with pytest.
- Each FE component driven red→green with vitest (`web/src/test/queryWrapper.tsx` for
  provider-wrapped hooks/components).
- Full gate once at the end: `uv run coverage run -m pytest && uv run coverage report`
  (100%), `uv run ruff check && uv run ruff format --check`, `uv run ty check`,
  `cd web && pnpm run typecheck`.
- Live smoke optional (topic-hub item: open several chats + a workflow, switch via
  dropdown, rename + delete in the modal, confirm sort order).
