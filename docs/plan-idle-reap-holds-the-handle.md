# Plan: a sandbox handle is always reachable from the registry

## 問題(2026-09-15,master `ced4659d`)

`tests/api/test_idle_kill.py::test_idle_killer_reaps_session_past_threshold` 自 #804 合併起在 CI
**四中四紅**(#804、#806、#805 兩次),本機單跑綠;本機開 coverage(`COVERAGE_PROCESS_START`)單跑
**三中三紅**,而且切回 #804 之前的 `3b8765a0` 一樣紅——所以不是 #804 帶進來的缺陷,是 #804 讓既有的縫
每次都被撞到。

**縫在哪**(對著 log 讀出來的,`registry.py:684–747`、`turn_context.py:837`):

1. 一個 turn 開始時 `turn_context` 先 `registry.session(item)` 建 session(`last_active` = 建立當下,
   `handle = None`),接著組 context、等模型、直到第一個需要 sandbox 的 exec 才 `ensure_handle(session)`。
2. idle killer 的 tick 落在 1 和 exec 之間:看到「有 session、沒 handle、時間戳超過 threshold」,走
   `registry.py:743`「no sandbox to reap」分支,**把 session 從 `_sessions` 刪掉**。
3. turn 手上還握著那個物件;`ensure_handle(session)` 只往物件上寫 handle,不回頭看 `_sessions`。
   sandbox 建好了,登記簿上沒有它:idle reap、`close_all` 都看不到。**孤兒。**

P33(#775,`6917a594`)修的是同一個洞的另一半——`ensure_handle` 握著 lock 期間的 tick——用
`s.lock.locked()` 跳過。lock 還沒拿之前的這段沒蓋到。

**為什麼 #804 之後每次都撞到**:cluster sweep 和 help 索引 job 現在在第一個 turn 進行中跑(log 裡
兩者都夾在步驟 1 和 exec 之間),加上 CI 的 coverage,步驟 1 到 exec 從 <0.1 秒拉到 0.26 秒,測試的
threshold 是 0.1 秒,tick 每次都落在縫裡。

**線上影響**:threshold 8 小時,實際碰不到;碰到也只是一個 sandbox 沒登記,`kind: http` 的 host 有自己
的 idle TTL 回收。**真正的代價是 CI**:api-1 從此每條 PR 都紅,那個 shard 另外 350 條測試形同關掉。

## 決定

| # | 問題 | 決定 | 為什麼 |
|---|---|---|---|
| 1 | 修哪裡 | **`ensure_handle` 裡,handle 拿到的那一刻保證 `_sessions[item]` 指得到它**(登記簿沒有就把呼叫者的物件放回去;登記簿已經是別的物件就把 handle 抄給它) | 判準裝在值被做出的地方。handle 只有一個製造者,每扇門(chat turn、workflow turn、WUI callTool、file route)都經過它;「turn 標記我在用」要三扇門各加一次,漏一扇就回來 |
| 2 | P33 的 `lock.locked()` 留不留 | **留** | 它擋的是「acquire 進行中被刪」造成的無謂 churn(刪了又放回);不變量由決定 1 保證,兩者不是同一條規則的兩份 |
| 3 | 測試的 0.1 秒動不動 | **不動** | 那條測試是對的、是偵測器;放寬只是把縫藏起來 |
| 4 | 要不要順便讓 kill_idle 不刪沒 handle 的 session | **不** | 一顆 pod 活著期間每碰過一個 item 就留一筆,是小洩漏但是洩漏;而且決定 1 已經讓那個刪除無害 |

## Phases

| phase | 內容 | 驗收 |
|---|---|---|
| P1 | 這份 plan | — |
| P2 | **先紅**:(a) e2e 用 event 強制縫——runner 在 `ensure_sandbox` 之前卡住、測試直接 `kill_idle`、放行、斷言 turn 後 sandbox 被 reap(不靠時序);(b) registry 單元:session 被 drop 後 `ensure_handle` 回來,`_sessions[item]` 指得到 handle;(c) 登記簿已被別的物件取代的分支。**再綠**:`ensure_handle` 補不變量。突變(拿掉不變量)→ (a)(b)(c) 與既有 timing 測試(coverage 開)都紅 | 既有 timing 測試 coverage 開 5/5 綠;`ruff`/`ty`;registry.py 那段註解補上另一半 |
| P3 | 推、draft PR、CI;推前自己三把鏡頭 | CI api-1 綠 |
