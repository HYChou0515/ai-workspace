# Plan — Issue #455：PM 前端 E — 協作 + 即時同步（SSE / feed / @提及 / 唯讀 gate / presence）

> `/grill-me` 定案(繁中)。#455 是 epic **#448** 的 follow-up,**stack 在 P1 分支**（`worktree-issue-448-pm-fe-foundation`,PR #457 未 merge）上。獨立 worktree `worktree-issue-455-collab`。

## 背景與探勘落差

原計畫假設「SSE seam 只需接線」,但探勘揭露:**後端 entity 寫入不發任何 broadcast** —— entity CRUD routes 連 `turn_engine` 都沒接,`EntityStore` 只呼叫 in-process `on_write` sink,agent-tool 的 entity 檔寫入也不發 `file_changed`。只有 raw Monaco 存檔會發 `file_changed`(而 FE 只拿去 invalidate `qk.files`)。且 **presence 完全 greenfield**。所以「peer 看到我的編輯」與 presence 都需動後端 → #455 定位改為 **full-stack**。

既有可複用:
- `useEntityWrite`（P1）已有 `canWrite` 旗標 + `invalidate`（SSE seam）。renderer 全走它,一翻旗標全 app 寫入入口失效。
- `item.permission` + `created_by` 已透過 `getAppItem` 到 FE（`AppItem` 的 `[field]: unknown`）;`lib/permission.ts` 有 verb 詞彙 + role 映射。
- `GET /activity` + `useActivity`（20s polling)已記 `entity_created`/`entity_updated`（`ref.investigation_id`）—— 但**全域、in-memory、200 上限、未持久化**（best-effort）。
- `useAgent` 訂閱 per-item `/stream`;`file_changed`（`FileChanged{path,by,kind}`）與 `user_message` 是 broadcast-only 事件。SSE broadcast 是 **per-pod in-memory**（非跨 pod）。
- @提及:`useAgent.mention` + `UserPicker` + 通知鈴已存在（「來看」summon）。

## grill 定案

| 題 | 定案 |
|---|---|
| 範圍 | **full-stack**（後端 broadcast + presence + 全 FE）—— 因 peer-sync + presence 需後端,破壞原「純 FE」前提 |
| 唯讀 gate 來源 | **前端從 `item.permission` derive `canWrite`**（複用 `lib/permission.ts`,對齊 #303-310 permission，非 members;PM 無 members 欄）→ 中央翻 `useEntityWrite({canWrite})` |
| 即時同步事件 | **共用 EntityStore 寫入路徑 publish `file_changed`**（`records_path/N.md`）→ human HTTP 與 AI agent-tool 寫入**同一機制**,C3 與 peer-sync 一併涵蓋 |
| presence | `_WorkspaceSession` subscriber tag `user id`（`get_user_id`）+ sub/unsub 發 roster 事件走**現有串流**;FE 頭像 stack。**per-pod ephemeral**（與現有 SSE 架構一致;跨 pod 同 #202/#349 另論） |
| @提及 | **複用既有** `useAgent.mention` + `UserPicker`;entity body/留言 mention 屬 **C2**（#453），不在 #455 新增 |

## Phases（flat-integer,commit per phase）

| Phase | 內容 | 層 | 主要檔 |
|---|---|---|---|
| **P1** | **唯讀 gate**:`useItemCanWrite`（`item.permission` + `useCurrentUser` + owner derive,對齊後端 write 授權語意）→ `AiYamlRenderer` 灌 `useEntityWrite({canWrite})`。renderer 早尊重 `canWrite`,一翻全 app 寫入入口失效 | FE | `hooks/useItemCanWrite.ts`、`lib/itemPermission.ts`、`AiYamlRenderer.tsx` |
| **P2** | **即時同步**:後端在共用 entity 寫入路徑 publish `FileChanged(records_path/N.md)`（human route + agent tool 都走 EntityStore → 一個 broadcast sink 接 `turn_engine`）;FE 在 item `/stream` 收 `file_changed`（path 屬本 item）→ invalidate `qk.entities.list` prefix。涵蓋 §C3 + §E 即時更新 | BE+FE | `api/entity_routes.py`/`entity/store.py` 寫入 sink、`api/app.py` 接線;FE `useEntityLiveSync.ts`（或擴 useAgent handler） |
| **P3** | **活動流 feed**:新元件 over `useActivity`,per-item filter（`ref.investigation_id === itemId`）,時間軸 + 點一則跳到該 entity | FE | `components/ActivityFeed.tsx` |
| **P4** | **presence**:`_WorkspaceSession` subscriber tag user id + sub/unsub 發 `presence` roster 事件;FE 新 hook 讀同串流 → 頭像 stack | BE+FE | `api/turns.py`、`api/events.py`;FE `hooks/useItemPresence.ts`、`components/PresenceBar.tsx` |

## ⚠️ 標記
- **broadcast per-pod**:即時同步/presence 只在同 pod 內生效;跨 pod 一致性同既有 #202（sticky routing）/#349,不在本 issue 解。
- **ActivityLog 未持久化**:feed 是 best-effort(重啟/換 pod 會空);持久化屬後端另案。
- **後端 CI**:full-stack → 動 Python,本 worktree 需 `uv sync --all-extras`;100% 覆蓋 gate 對新 Python 生效。

---

**關聯**:#448 P1（`useEntityWrite` 兩接縫）、#303-310（permission）。stack base = `worktree-issue-448-pm-fe-foundation`;**merge 順序**:先 #457（P1）再本 PR。
