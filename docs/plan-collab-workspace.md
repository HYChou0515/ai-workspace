# Plan: #43 workspace 多人協作

> 經 `/grill-me` 走完決策樹後定案。可勾選追蹤文件 —— 每完成一階段打勾並 commit。
> 範圍縮在 **檔案 + chat(agent)**；**notebook/jupyter 協作不做（for now）**。
> 分支 `feat/issue-43-collab-workspace`。本地 commit，不 push。

## 鎖定決策

| 項目 | 決定 |
|---|---|
| 範圍 | 檔案 + chat；notebook 不做 |
| member 能做什麼 | 平等讀寫 + 用 agent（無 role 分層） |
| agent 並發 | **序列化排隊**（一 investigation 一次一 turn，FIFO）；任何人可 Stop（只停當前 turn，不清佇列） |
| 即時性 | **廣播**：send/subscribe 分離，per-investigation SSE，大家即時共看 |
| 存取 | 成員制、**預設全員**（v1 不做 enforcement / 管理 UI，只正確歸屬） |
| 檔案並發 | **last-write-wins** + 廣播「file changed by Y」（FE refetch/提示） |
| 訊息歸屬 | 填 `Message.author=get_user_id()`，FE 每則 user 訊息顯示 UserChip |
| presence | v1 跳過 |
| undo (#38) | 任何人可 undo，結果走廣播 |
| multipod | 沿用現有 per-investigation sticky routing（`upstream-hash-by: $rca_ws_key`）；新 endpoint 都放 `/investigations/{id}/` 底下自動同 home pod；queue/worker/pubsub 全 in-memory co-located |

dev 無 SSO：`get_user_id` 固定 `default-user` → 多人只能用「注入 get_user_id」測試驗證（比照 test_kb_share）。

## 核心改動
`api/turns.py`：抽出 event-reducer；KB chat 維持 `stream()`（per-requester、cancel-prior）；investigation 新增 `enqueue()` + per-investigation pub/sub 廣播 worker（序列化）。

## 階段（每階段：ruff/ty 清、後端 coverage 100%、FE typecheck+vitest+build 綠、commit、打勾）

- [x] **P1** — User 訊息歸屬 (BE)：`send_message` 填 `author=get_user_id()`（已驗；`author` 透過 specstar Conversation 序列化曝給 FE。FE UserChip 併入 P5 一起做）。
- [x] **P2** — Turn engine → per-investigation 序列化 **queue**：抽 `_TurnReducer`；`enqueue` 不再 cancel 前一個（回 completion future）；worker FIFO；`cancel_current` 任何人停當前。engine-level 測試。
- [x] **P3** — **廣播**：`_WorkspaceSession.subscribers`+`publish`；`subscribe()`/`publish()`；`GET /investigations/{id}/stream` SSE；`POST messages` enqueue+await 自己的 turn→202；worker publish 所有事件 + 新 `UserMessage`。events.ts 同步。POST 改 202（非串流），相關測試全改（serialize+Stop）。354 api 測試綠。
- [x] **P4** — 檔案 **changed 廣播**：write/mkdir/move/copy/delete endpoints publish `FileChanged(path,by,kind)`。agent 寫檔由 turn-done refetch 涵蓋。events.ts 同步。
- [x] **P5** — FE 改接：`streamAgentEvents`→`sendMessage`(POST enqueue) + `subscribeInvestigation`(GET /stream 持久訂閱)；`useAgent` 持久訂閱驅動 log、send 只 enqueue、`file_changed`→invalidate files、terminal→refetch citations；`reduceAgent` 加 `user_message`/`file_changed`；mock 加 per-investigation pub/sub。FE typecheck + 476 vitest + build 綠。

---

## 完工狀態（全部綠）
- 後端全測試：**1297 passed**（22 分鐘全跑）。coverage 維持專案 baseline（98%，未動到的舊檔案有既存 gap）；#43 新碼 events/schema 100%、turns 99%（僅無限串流 `async for` 的 exit partial）、其餘 gap 皆既存。
- ruff/format 乾淨；ty 僅 1 個既存 baseline 診斷（app.py `ActivityLog.record`，非本次新增）。
- FE：typecheck + 476 vitest + build 全綠。
- commits：P1 record sender → P2 serialized queue → P3 broadcast → P4 file-changed → P5 FE + cleanup。本地未 push。
