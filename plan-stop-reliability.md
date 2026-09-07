# Stop 的可靠性

使用者回報兩件事，查下來是同一個區域的不同缺陷：

1. **「按下 Stop 之後要過好久才會真的停下。」**
2. **「訊息欄裡有東西時按 Stop，會把當前停下然後送出——但使用者只覺得東西不見了，AI 還在跑，像系統故障。」**

第二件的一半（「東西不見了」）已經修掉：送出時本地就畫出自己的訊息，不再等後端廣播。
見 `fix(chat): draw the sender's own message when they send it` 那一組 commit（真瀏覽器量測：
注入 5 秒後端延遲下，泡泡從 2823ms → 34ms，且廣播到達後仍只有一顆）。本計畫處理其餘部分。

---

## 判準

逐項憑感覺會漏，所以先立一條規則：

> **還在「決定要做什麼」的，殺得；已經在「記錄已經發生的事」的，不能殺。**

推論：
- 一個還沒寫出任何東西的準備工作 → 殺得（沒有持久效果）
- 一個只在最後才寫入的工作 → 殺得（全有全無，中途殺不留半成品）
- **一個中途會留下狀態、而且沒人看得出那狀態不完整的工作 → 不能殺**
- `persist` / `_flush_item` 這種「記錄已經發生的事」 → 永遠不殺。殺了只會讓重跑重複副作用
  （與 `write_record` 免受 quota 檢查是同一條理由，見 CLAUDE.md）

---

## 缺陷盤點

一個 turn 期間會跑、可能活過 Stop 的東西，以及現況：

| # | 項目 | 位置 | 現在殺得掉？ | 本計畫 |
|---|---|---|---|---|
| 1 | LLM 推理／回應串流 | turn task 內 | ✅ | 不動 |
| 2 | 內建工具（read_file / kb_search / …） | `agent/tools.py` | ✅ | 不動 |
| 3 | **內建 `exec`** | `sandbox.exec` | `local` ✅／**`http` ❌** | **P2** |
| 4 | **第三方工具** | `tooling/registry.py:423` → 同一個 `sandbox.exec` | 同 3 | **P2**（同一個修法） |
| 5 | Sub-agent（`ask_knowledge_base` / `ask_wiki` / `run_subagent`） | `subagent_bridge.py` | ✅ | 不動（內部 exec 靠 P2） |
| 6 | VLM | `agent/deck/loop.py:215` `to_thread` | ❌ 執行緒必定跑完 | **P5**（先量） |
| 7 | **compaction（會叫 LLM）** | `chat_send._send` | ❌ 取消碰不到 | **P3** |
| 8 | **turn 前置作業** | `chat_send._send` | ❌ 取消碰不到 | **P1**（不殺，改成記住 Stop） |
| 9 | Goal 自動續轉（#613） | `_goal_tasks` | ✅ 有 epoch stand-down | 不動 |
| 10 | 下班窗自主長跑（#615） | 同 9 | ✅ | 不動 |
| 11 | `persist` / `_flush_item` | turn 結束時 | ⚠️ | **刻意不殺**（判準第四條） |
| 12 | 心跳 `_beat` | detached | ✅ 自己會停 | 不動 |
| 13 | `_drain_and_persist` | detached | ⚠️ 刻意的，非 Stop 路徑 | 不動 |
| 14 | 同步 specstar 呼叫 | 全域 | ⚠️ 非殺不掉，是**延後**到它回來 | 不在範圍（見文末） |

UI 側另有三項：

| | 缺陷 | 位置 |
|---|---|---|
| A | Stop 一按就解除 composer 守衛 → 可送進沒真的停下的 turn | `useChatSession.tsx:651` + `AgentPanel.tsx:567` |
| B | Send/Stop 同插槽同尺寸互換，控制項在游標下換意思 | `AgentPanel.tsx:1291`、`KbChatPanel.tsx:407` |
| C | 「正在停止這一輪…」只有提示語、沒有狀態 | `AgentPanel.tsx:1301` |

> **證據等級**：除了泡泡那兩個 commit 有真瀏覽器量測，本表其餘皆為**讀碼推論**，沒有跑起來驗過。
> 每個 Phase 的驗收都要求對「未修版本」驗紅，那才是證據。

---

## Phase 1 — 讓 Stop 在 preamble 期間被記住

**問題。** `turns.py:790` 的註解自己寫了這個失敗模式：turn 是在 preamble **之後**才 stamp
cancel epoch。所以 Stop 落在 preamble 期間，`advance()` 加的那一格，turn 一 stamp 就已經是
新值，`> my_epoch` 永遠不成立 → **Stop 被靜默吃掉，整輪照樣從頭跑到底。**

這正是使用者回報的「按了很久才停」：那五秒的空窗，剛好也是 Stop 完全無效的時間窗。

**為什麼不是「把 preamble 殺掉」。** 因為 preamble **不是原子的**。殺在冷 sandbox 還原中途會留下
一個「活著但只還原一半」的 sandbox：

- `.ready` 不會被設（`sync/sandbox_sync.py:146` 的 `mark_ready` 在還原完成後才呼叫）
- 但 `_warm` 只探 `exists`、**不看 `is_ready`**（`files/facade.py:290`）
- 全專案掃過：**`is_ready` 只有鏡像在讀**（`sandbox_sync.py:166` / `:260`），讀寫路徑都不檢查

結果是「這次的工作區少了一半，而且沒有任何地方會發現」，寫入也會 route 進去，而且**不會自癒**
（還原被中斷，沒人會再呼叫 `mark_ready`；下一輪 `exists` 成功就直接沿用）。durable 是安全的
——鏡像的 `is_ready` 前後三明治會擋住（#366 就是為此設計）。

**修法。** 把 cancel epoch 的 stamp 從「turn 被 dequeue」提前到「使用者訊息持久化」
（`chat_send.py:708` 之後）。Stop 落在 preamble 期間 → 那個早就記下的值過期 → turn 一建立就
發現自己過期、直接 stand down。preamble 照樣跑完（不會壞），但那一輪不跑。

機制已經存在，只是 stamp 的時間點錯了。**不新增機制、不動 shield。**

**驗收。**
- 新增測試：在 preamble 期間呼叫 `cancel_current`，斷言 turn 從未執行（runner 沒被呼叫）。
  對未修版本必須紅。
- 突變：把 stamp 改回原位置 → 該測試必須紅。

**檔案。** `src/workspace_app/api/chat_send.py`、`src/workspace_app/api/turns.py`

---

## Phase 2 — 生產環境的 exec 真的被殺掉（含第三方工具）

**問題。** app 的 turn 被取消 → `http_client.py:450` 的 `async with client.stream(...)` 關掉連線
→ host 端 generator 被關 → 但 `sandbox-host/src/sandbox_host/app.py:226` 是：

```python
    finally:
        await task
```

**是 `await task`，不是 `task.cancel()`。** 所以 host 上的 `sandbox.exec` 從來收不到
`CancelledError`，`local_process.py:649` 那條 `except CancelledError: await _terminate()`
（SIGKILL 整個 process group）**走不到**。指令會跑到自己結束，或撞 host 的 `exec_timeout` /
`log_timeout`（各預設 60 秒）。

第三方工具走的是同一條 `sandbox.exec`（`tooling/registry.py:423`），所以一個修法涵蓋兩類。

這是全表唯一**不只影響觀感**的缺陷——Stop 之後 agent 的指令還在改 workspace 裡的檔案、還在
燒 CPU 額度。

**前置：必須先實測，不能讀碼定案。**

Starlette 在 client 斷線時到底會不會關掉那個 async generator？很多情況要等下一次寫入才偵測
得到——一個安靜跑（不輸出）的指令可能永遠偵測不到。**先花時間確認這件事，再選路。**

**修法 A（小，前提成立時用）。** `finally` 改成先 `task.cancel()` 再 await（吞掉
`CancelledError`）。殺行程的機制已經寫好也有測試，只是沒人扣板機。

**修法 B（確定，前提不成立時用）。** 不依賴斷線偵測：新增 `POST /sandboxes/{rid}/exec/cancel`，
app 端在 turn 取消時明確呼叫。要新增端點、app 要記住哪個 exec 在飛。sandbox-host 與 API 同一條
CI/CD，加端點不用為版本歪斜設計降級路徑。

**不要在兩者之間硬凹。** 前提成立走 A，不成立直接走 B。

**驗收。**
- 整合測試：起一個長時間指令，取消 turn，斷言行程真的不在了（不是只斷線）。
- 對未修版本驗紅。

**檔案。** `sandbox-host/src/sandbox_host/app.py`；修法 B 另加 `src/workspace_app/sandbox/http_client.py`

---

## Phase 3 — compaction 可被取消

**問題。** compaction 會叫 LLM（`chat_send.py:318` `await self._compactor.summarise`），
但它跑在 `_send` 裡，而 `cancel_current` 只認識 `session.current_turn`——碰不到 `_inflight`
（`chat_send.py:215`，而且那是個 **set**、沒有 keyed）。

**它可以安全地殺**：寫入只在 `summarise` 回來之後才發生（`chat_send.py:325-326`），本來就是
全有全無。中途殺不留半個摘要，只是白花一次 LLM——下一輪要壓再壓。

**結構問題。** #7 和 #8 今天**跑在同一個 task**。所以「殺 7 不殺 8」不可能靠取消那個 task 達成。
→ **把 compaction 拆成自己的 task**，用 `engine_key` 登記到 engine 上，`cancel_current` 一併取消。
preamble 留在原本的 task，不動（P1 已用 epoch 處理它）。

**shield 不擋這件事。** `asyncio.shield(task)`（`chat_send.py:280`）只是讓「等待的那一端」被取消
時不波及 task；task 物件本身還是能被別人 `cancel()`。所以**不用動 shield**——斷線仍然不殺工作
（那正是 shield 的用意），Stop 明確殺。兩者本來就該分開，現在只是沒有人拿得到那個 handle。

**驗收。** 新增測試：compaction 進行中呼叫 `cancel_current`，斷言 `summarise` 被取消且
**沒有摘要被寫入**。對未修版本驗紅。

**檔案。** `src/workspace_app/api/chat_send.py`、`src/workspace_app/api/turns.py`

---

## Phase 4 — Send / Stop 拆成兩顆獨立的 icon 按鈕

**問題。** 兩顆按鈕現在佔**同一個插槽、同樣 `padding: "6px 14px"`、同樣位置**，靠
`log.streaming` 互換（`AgentPanel.tsx:1291`、`KbChatPanel.tsx:407`）。控制項在游標下換意思：
你打好字往「送出」的位置點下去，那時卻是 Stop → 停掉；`cancel()` 立刻把 `streaming` 翻 false，
同一個位置馬上變回 Send → 再點一下就送出去了。這就是使用者說的「停下然後送出」。

而 `cancel()` 那個樂觀翻轉（`useChatSession.tsx:651`）同時**解除了 composer 唯一的守衛**
（`AgentPanel.tsx:567` 的 `if (log.streaming && !othersTurn)`），所以訊息會排進一個還沒真的停下
的 turn。

**修法。**
- Send / Stop 各自獨立、常駐、各有 disabled 狀態，皆為 icon（`aria-label` + `title` 必補；
  `⌘↵` 提示要留著——換成 icon 之後它是快捷鍵唯一的線索）
- 新增 `stopping` 狀態：按下 Stop → Stop disabled、Send 維持 disabled，**直到 turn 的終止事件
  才解除**，而不是按下去就翻。現有的 `setComposerHint("正在停止這一輪…")`（`AgentPanel.tsx:1301`）
  已經在講這件事，但畫面沒有對應狀態——提示在描述一個畫面不承認的事實
- **兩處都要改。** `KbChatPanel` 語意還不同（KB chat 的新訊息**會取消**前一則，不是排隊），
  同一組按鈕在兩處代表不同承諾，要分別想清楚
- 決策（已與使用者確認）：**Send 對自己的 turn 也放行排隊**。後端本來就照排，
  「別人的 turn 可排、自己的不行」這條不對稱規則取消

**必須排在 P1 / P2 之後。** 先做 UI 的話，誠實的「停止中」會掛到 60 秒——把「假裝停了」換成
「看起來當掉了」，體感更糟。P2 修完，「停止中」才會短到付得起。

**驗收。** FE 測試：`stopping` 期間兩顆都不可按；終止事件到達才解除。突變（拿掉 `stopping`
狀態）必須讓測試紅。兩個面板各自要有。

**檔案。** `web/src/hooks/useChatSession.tsx`、`web/src/pages/investigation/AgentPanel.tsx`、
`web/src/pages/kb/KbChatPanel.tsx`

---

## Phase 5 — VLM 的 `to_thread`（先量再決定）

`agent/deck/loop.py:215` 的 `await asyncio.to_thread(io.vlm.collect, ...)` 是整個 agent 路徑上
**唯一真正取消不掉**的東西——`to_thread` 取消的是 future，**執行緒本身會跑完**。

**先量它多常走到。** `deck/` 是簡報那條路（#533），可能根本不在一般聊天路徑上。一個月走不到
幾次的話，這個排最後甚至不做。

量完若值得做：

- **(a) 把 `vlm.collect` 改成真 async** — 它本來就是對模型端點的 HTTP 呼叫，litellm 有 async
  API。取消就會關連線。對的修法，動最多。
- **(b) 用 `on_chunk` 當取消點** — 那個 callback 已經存在，串流的 VLM 會頻繁回呼。在 callback
  裡檢查取消旗標並丟例外，把洩漏從「整個呼叫」壓到「一個 chunk」。外科手術式，不動傳輸層。
- **(c) 只加逾時** — 不算修好，只是封頂。

傾向 **(b)**。

---

## 不在本計畫範圍

- **#14 同步 specstar 釘住迴圈期間取消無法投遞。** 不是取消不掉，是**延後**到它回來。單次不長
  （PR#657 量過 `get` 59.79ms、CPU-bound），但連打幾十次就是好幾秒不可中斷。那是
  `project_api_sync_specstar_blocks_loop` 的題目，且該方向曾被撤回（GIL 讓丟執行緒無效），
  不要在這裡順手處理。
- **SDK 取消工具後不等它收拾**（`agents/run_internal/tool_execution.py:1833` 只掛 done_callback
  就 `raise`）。第三方行為；包一層會讓 Stop 更慢。**等 P2 修完，先量還剩多少延遲再決定。**
- **`_warm` 不看 `.ready`**（`files/facade.py:290`）。已知未修的洞（見
  `project_warm_read_ignores_ready`）。P1 選擇不殺 preamble，正是為了不把這個洞從偶發變常態；
  但洞本身仍在，值得獨立開一張。
- **回覆本身的延遲。** 本計畫只處理 Stop。那五秒的真正嫌疑是 `build_chat_turn` 裡的 `/tokenize`
  探測——`context_budget.py:50` 記載 `catalog_limit` 解 `ollama/*` 要問 daemon 且**完全沒有逾時**，
  對不回應的位址量到 **129,781 ms，整個事件迴圈凍住**。那是另一件事，而且可能更值錢。

---

## 執行順序與紀律

```
P1 (epoch 提前) → P2 (exec 真的被殺) → P3 (compaction 拆 task) → P4 (UI) → P5 (VLM，先量)
```

- **每個 Phase 一個 commit**，flat integer，不要 1a / 1b
- **TDD**：每個 Phase 都要有**新增的、會紅的**測試；改既有斷言不算
- **修復必須對未修版本驗紅**，否則是假綠、零防護
- 本機只跑 **targeted** 測試 + `ruff check` / `ruff format --check` / `ty check`；
  全套交給 CI（一輪 20–30 分，別本機空等）
- 秒級的閘門過了、只剩唯讀工作（review / 寫 PR 內文）就**先推、開 draft PR**，讓 CI 跟你平行跑
- **P2 的前置實測沒做完之前，不要開始寫 P2 的程式碼**

---

## 執行結果（全部完成）

| Phase | commit 標題（用 `--grep` 找它） | 結果 |
|---|---|---|
| P1 | `P1 a Stop pressed while the turn is still being prepared…` | Stop 在 preamble 期間不再被靜默吃掉。**機制在第一輪 review 後換過**，見下 |
| P2 | `P2 Stop now actually kills the command running in the sandbox` | **前提實測成立 → 走修法 A**；`finally` 改成 cancel |
| P3 | `P3 Stop reaches the summariser…` | `run_interruptible` + compaction 拆 task；新增 `stopped` outcome |
| P4 | `P4 Send and Stop stop being the same button` | 拆兩顆 icon 按鈕 + `stopping` 狀態；兩處都改 |
| P5 | `P5 a stopped deck turn lets go of the model call` | `on_chunk` 當取消點；洩漏從整個呼叫壓到一個 chunk |

> **這張表不列 commit hash。** 它原本列了五個，而分支之後 rebase 到往前 93 個 commit 的
> master，那五個全部變成任何分支都構不到的孤兒——**而表看起來完全正常**。標題找得到，hash 不會。

### P2 的前置實測 — 答案是「會」

用和 `_exec_ndjson` 一樣的形狀（背景 task 餵 queue、generator 讀、`finally: await task`）
對真的 uvicorn 量：

```
--- chatty (指令持續有輸出) ---  finally: 斷線後 0.03s 觸達
--- quiet  (第一個 frame 之後全靜音) ---  finally: 斷線後 0.03s 觸達
```

兩種都觸達，而且觸達時 task 還在跑。所以**斷線偵測不是問題**，備案的
`POST /exec/cancel` 端點不需要做。這是計畫裡唯一「不實測就不准寫程式」的關卡。

### 每個 Phase 的驗證

全部都對**未修版本**驗過紅，且突變探針各自咬到預期的測試：

- **P1** — 拿掉 stand-down → 兩條 preamble 測試紅；拿掉 `chat_send` 的 `epoch=` →
  **只有走真入口那條紅**（engine 層那條照樣綠，這正是它存在的理由）；`>` 改 `>=` →
  「下一則訊息不該繼承上次的 Stop」那條紅。
- **P2** — 未修版本 `aclose()` 等完整個指令 → `TimeoutError`。
- **P3** — 未修版本 Stop 碰不到 summariser → `TimeoutError`。⚠️ 前兩次的紅是**用錯
  engine_key** 跑的（預設對話 key 是 item_id 不是 chat id），證據被污染，已用正確的 key 重驗。
- **P4** — `cancel()` 改回 `streaming: false` → `stopping` 兩條紅。⚠️ 「終止事件忘了清
  `stopping`」這個突變**第一次沒被抓到**——hook 那條測試的終止路徑會順便重新補水，
  `reconcileSnapshot` 幫忙清掉了。補了一條直接打 reducer 的測試才咬得到。
- **P5** — 拿掉 `stopped.set()` → 紅。未修版本量到 Stop 後又多跑 **147 個 chunk**。

### 順手修掉的（原計畫沒列）

- `max_turns_exceeded` 是唯一只清 `streaming` 的終止事件，沒有註解說明；四處重複的重置
  收斂成 `TURN_OVER` 一處。跑完 render+review 中途用光步數的 turn 原本會一直宣稱自己在壓縮。
- `reconcileSnapshot.test.ts` 手刻了一份完整的 `AgentLog` 字面值，加第一個新欄位就壞；
  改成展開 `EMPTY_LOG`。
- 四條釘住舊規則的既有測試**沒有刪除**，各自遷移到取代它們的規則上。

### 仍未做

- **沒有 push、沒有開 PR**（照使用者規矩）。因此 **CI 未跑**，本機只跑了 targeted。
- **沒有在真瀏覽器按過 P4 的新按鈕。** 測試綠不等於畫面對。
- `uv run ty check` 有 1 個 diagnostic：`pandera.pandas` 解不到，是這棵 worktree 沒跑
  `uv sync --all-extras`（CLAUDE.md 有記），與本次改動無關；`tests/agent/` 有 5 條
  `test_infer_modules` 因同一原因紅。


---

## 第一輪對抗式 review 之後

四個 lens（defect / conformance / veracity / regression）平行跑過，各自獨立。**一致性判定：
五個 Phase 都做到計畫描述的事，鎖定決策沒有回退，「不在本計畫範圍」一項都沒被順手做掉，
而計畫裡宣稱的每一個突變都被獨立重跑並復現**（連 P5 的「147 個 chunk」都分毫不差）。

找到的是別的東西。

### 換掉的機制：P1 的站下

三個 lens 從三個方向指到同一行。原本的做法是「每則訊息在持久化時蓋 cancel epoch，出列時
比對」，但 Stop 對一個 key 只 bump 一次 epoch，所以**所有在 Stop 之前蓋章的訊息**都會被站下
——包含別人早就排進 FIFO 的。這牴觸 `cancel_current` 與 `chat_routes` 兩處寫死的契約，而且
P4 開放「自己的 turn 進行中也能送」之後變得很好走。

守衛之所以沒抓到：本來守這件事的 `test_cancel_current_stops_only_the_running_turn_then_next_runs`
呼叫 `enqueue` **沒帶 epoch**——也就是生產環境已經不走的那條路。

第二個錯：站下不呼叫 `on_complete`，什麼都不存，打破 `agentLog` 寫死的「turn 一定以某個
持久化結果收場」，於是重整後畫面顯示「還在回覆」達 30 分鐘，在那狀態按 Stop 兩顆按鈕一起死。

**改成 per-send 的 `PendingTurn` 憑證**：持久化前登記、`enqueue` 時註銷。Stop 只標記「還在
準備中」的；已進佇列的碰不到。站下時走 `on_complete` 寫下和正常取消相同的 cancelled 標記，
並發同一顆終止事件。

代價要講明白：**跨 pod 的「preamble 期間 Stop」不再被涵蓋，那一輪會完整跑完。**

我原本在這裡寫「turn 開始後仍由 `_watch_epoch` 中止，和 master 一樣」——**第二輪 review
並排實測，那句是假的**：`my_epoch` 在出列時才讀，那時對方 pod 的 `advance()` 已經含在讀數裡，
`> my_epoch` 永遠不成立。缺口是真的，我編的是那句安慰。

**刻意不修。** 把 epoch 改在 `preparing` 蓋章、拿去當 watcher 的基準線，會讓「排在執行中
turn 後面的訊息」在那個 turn 被 Stop 時一起被砍——正是這整個機制要消除的連帶損害，
只是搬到下一層。跨 pod 取消是 #349 的題目，該用 #349 的解法。

### 其他修掉的

- **送出被拒絕時，`drawOwnAsk` 畫的那則沒被收回**（`retractOwnAsk`）。它不只留在畫面上：
  沒有 `at` 的 entry 被 `turnsFromEntry` 算成一個 turn，「undo 到這裡」因此多刪一個真 turn，
  **不可逆**。gateway cut（502/504）刻意不收回——那裡訊息可能真的在跑。
- **`stopping` 會卡死**：新的送出現在會清掉它（`retryTurn` 是 `cancel()` 接 `send()`）。
- **`forget` 現在也收掉 `preparing` 與 `pending_turns`**：刪 chat 的下一行就 delete
  conversation，summariser 原本會跑完再對已刪除的 conversation 寫入。
- **送出的判準下沉成一個 `sendRefusal()`**：`submit` / `onChip` / `onAnswerQuestion` / chip
  按鈕的 `disabled` 原本有**四份**拷貝，規則改了只有兩份跟上——正是計畫自己宣稱修掉的
  「旁觀者被鎖住」。

### 補上五條「刪掉一行、整套照樣綠」的守衛

`reconcileSnapshot` 的 `stopping` 攜帶（兩個方向各一條）、`compact` 的 `except CancelledError`、
`stopped` outcome 走 `POST …/compact` 端到端、站下的 live `RunCancelled`、KbChatPanel 的 Send
守衛。**最後一條原本的測試是碰巧綠的**——`submit` 會 `setDraft("")`，所以它量到的是
`!draft.trim()` 而不是 `log.streaming`。

### 更正的不實宣稱

- 「icon-only 按鈕沒有 `aria-label` 就沒有 accessible name，`title` 不算」——**實測是錯的**，
  拿掉 `aria-label` 之後 `getByRole("button", { name })` 仍然找得到。註解已改成說明為什麼
  仍然明寫 label，而不是宣稱一件假的事。
- `HttpSandbox.exec` 的 `SandboxBusy` 註解說「the command is still RUNNING on a busy host」
  ——P2 之後不再成立（read timeout 關掉串流現在會殺掉指令）。已更正。
- P4 的 commit message 說 `max_turns_exceeded` 「listed two thirds of it」，實際是 6 個欄位
  裡的 2 個，也就是三分之一；程式碼裡的註解是對的，commit message 不對。歷史不改寫，記在這裡。

### 仍未做

- **沒有 push、沒開 PR，所以 CI 從頭到尾沒跑過。**
- **P4 的新按鈕仍然沒有人在真瀏覽器按過。**
- P2 的驗收字面要求整合測試（「斷言行程真的不在了」），交付的是 mock 單元測試 +
  既有的 `test_exec_kills_whole_process_group_on_cancel`（真的 spawn、驗孫行程死掉）。
  兩半是靠推論接起來的，**沒有任何一條測試從「取消 turn」一路走到「行程不在了」**。


---

## 第二至五輪 review 之後

第一輪之後又跑了四輪，每一輪都只問前一輪收斂出來的那一個問題。**每一輪最嚴重的發現都是
上一輪修法造成的**——那正是「還沒收斂」的判準——但範圍是單調縮小的：

| 輪次 | 最嚴重發現的範圍 |
|---|---|
| 1 | 整個佇列：Stop 吃掉別人已排隊的訊息 |
| 2 | 準備窗：拋例外時留下沒有結局的對話 |
| 3 | 一個述詞的成員：404/410 漏掉 |
| 4 | 那個述詞立足的**前提**：狀態碼根本無法回答「寫進去了沒有」 |
| 5 | 另一個軸：**第二個非 HTTP 消費者**的復原機制被拆掉 |

### 最大的契約變更：寫入即受理

`POST …/messages` 現在在**訊息寫進 thread 之後**就回 202；準備階段之後的失敗改為記在 thread
裡並發到串流，和 turn 自己的失敗一直以來的做法一致。

原因是第四輪實測出來的：狀態碼**不是路由選的**，是 `create_app` 裡一張**按例外型別**的
handler 表決定的，那張表不知道例外拋在請求的哪個階段。同一個 404 既是被撤權的訪客（路由），
也是準備中途消失的 skill 資料夾（窗內，而 `apps/skills.py` 明說它會在那裡拋）。所以**沒有
任何狀態碼能承載「寫進去了沒有」**，只能由端點自己回答。

前端因此不再推論任何東西：收到答覆的失敗一律收回草稿泡泡，**沒有答覆的**（連線中斷、
`fetch` 直接 reject 而沒有狀態碼）一律保留。

**但 driver 例外。** `OffHoursGoalSweeper` 是 `send` 的第二個、非 HTTP 消費者，它靠例外釋放
整段的認領好讓下一 tick 重試（「must not cost that chat its night」）。吞掉例外等於告訴它
回合開始了：認領扣到天亮、一個 turn 都沒跑。所以 `driven_by` 有值時仍然 raise——兩個
消費者各拿到自己能據以行動的東西。

### 順手修掉的既有缺陷（不在本計畫範圍，但一行）

`start_offhours_round` 用了只在 `TYPE_CHECKING` 下 import 的 `_MessageBody`，**每次都
NameError**——#615 的下班窗從來沒有真的啟動過任何回合。更糟的是 `offhours_rounds_used += 1`
在拋出之前，而 `max_rounds` 是跨夜累計、永不重置，所以一個開了下班窗的目標會在第一晚
三十分鐘內燒光終身額度、跑零個 turn，然後被永久濾掉，全程只有 log。

`ty` 和 ruff 的 F821 都把 typing-only import 當成綁定，所以兩個都看不到。**`TC004` 看得到，
而且全專案只flag 這一行**，已加進 `select`（把缺陷放回去驗過它會叫）。

### 我自己的三個習慣（這五輪最值錢的產出）

1. **承認缺口之後補一句沒人驗過的安慰。** 「28ms 證明修法有效」（修改前是 18ms）、
   「turn 開始後 watcher 會接手」（實測整輪跑完）。兩次都是誠實講出缺陷，然後自己加一句
   讓它聽起來沒那麼糟的話。
2. **宣稱「每條規則都用刪除驗過」然後沒驗。** 連續三輪，每輪由 reviewer 指出漏的那條；
   第三次是在一個點名前兩條漏網的 commit 裡又漏第三條。
3. **能推導的地方用列舉。** `{403, 507}` →「任何 4xx」→ 真正的錯是前提本身。前兩次修的是
   列舉的內容，第三次才修掉「用列舉」。

第 3 個是結構性解掉的。前兩個是習慣，只有對抗式 review 抓得到。

### 仍未做

- **沒有 push、沒開 PR，CI 從頭到尾一次都沒跑過。**
- **P4 的按鈕仍然沒有人在真瀏覽器按過**，而這五輪動了大量 UI 判準。
- P2 的驗收字面要求整合測試，交付的是單元測試 + 既有的 process-group 測試，兩半靠推論相接。
- 前端全套約每五次紅一次。第六輪量過：我的分支 2/10、當前 master 2/10，**每次受害檔案不同**
  ——跨檔案汙染，不是這個分支造成的。（先前寫成「歸給 `SkillsModal`」是錯的：那一輪其實
  3722 條全過，exit 1 來自別的檔案的 unhandled rejection。）
- 分支在 review 期間被 master 追過**兩次**（93 個、9 個），各 rebase 一次並重驗。

---

## 第六、七輪 review 之後

兩輪都只驗前一輪的修法，範圍繼續縮小：

| 輪次 | 最嚴重發現的範圍 |
|---|---|
| 6 | 修好 `NameError` 之後才跑得到的那個預算：起不來的回合仍被計費 |
| 7 | **上一輪的退款把唯一的上界一起拿掉了**；以及一個守在「不會先觸發的出口」的守衛 |

### 第七輪的兩個發現

**1. 我的守衛守在唯一不會先觸發的出口。** `useKbChat` 的 `send` 在 await 之後有四個寫狀態
的地方，我只守了 `finally`。實測：有守衛丟在 145 行（catch），沒守衛丟在 148 行（finally）
——**只是換了哪一行爆**。而且刪掉它 3722 條前端測試全綠，沒有任何東西守它。

修法是把守衛搬到**狀態被造出來的地方**：`useChatLog` 包住 `setLog`，兩種聊天傳輸的每一個
寫入者一次覆蓋；`useKbChat` 只留一個守它自己擁有的 `chatId`。兩個出口各有一條刪掉就紅的測試。

**2. 退款拿掉了唯一的上界。** 收費會用「燒光預算」停下重試——那是個上界，但它同時毀掉一個
只是那晚不順的目標的**終身**額度。只退款則讓 sweeper 每分鐘重試、永遠不停，每次寫兩則訊息
進使用者的 thread。兩個都不是任何人要的上界。

`stall_count` 才是。起不來的回合怎麼讀都是「沒有進展」，而 #615 早就決定了沒有進展要怎麼辦：
連續 `_STALL_LIMIT` 次就把目標停下交給人。順帶要處理的是 `unattended` 是從回合計數推導的，
而退款剛把它退回 0——一個從沒起來過的夜晚會**靜默交棒**，而且只會發生在半夜。sweeper 只在
窗內開火，所以它直接說，不從一個對這個結局失效的 proxy 推。

`_goal_followup` 同樣的 bump-before-send 也退款了（只包住 send 那一句）。

### 到這裡的驗證方式

五個突變探針、五個紅：stall 計數、`unattended` 覆寫、driver 的退款、前端兩個守衛。

### 真瀏覽器：親自按過了（2026-09-07）

起一個完整的 app（port 8247），**只有模型端點換成假的**——一個每 400ms 吐一個 token、
可以慢慢跑五分鐘的 OpenAI 相容端點，其餘（specstar、SSE、sandbox、前端 bundle）全是真的。
用真的 Chromium 驅動。

| 使用者當初回報的症狀 | 這次量到的 |
|---|---|
| 送出後要等幾秒才看得到自己的訊息 | 自己的泡泡 **17ms** 出現 |
| 按下 Stop 後要過好久才真的停下 | 點擊 11:09:09.452 → 假端點記錄 client 離開 11:09:09.42x，**約 30ms** |
| 訊息欄有東西時按 Stop，字不見了 | 草稿在按下當下與 5 秒後**都還在**（逐字比對） |
| AI 繼續跑、像故障 | thread 出現 `Cancelled.`，之後還能再送一次並正常回覆 |

按鈕狀態（全新 item，沒有任何 turn 跑過）：Stop 在但**不可按**、Send 空白時不可按、
打了字之後 Send 可按；串流中 Stop 變紅可按、Send 變灰。**兩顆永遠都在，同時只有一顆是活的。**

> 第一次量「靜止狀態」時我讀到 Stop 是可按的，那是量測污染——同一個 item 上還有前一次
> 跑剩的 turn 在跑。換全新 item 重量才是上表的數字。

**仍未在真瀏覽器驗過的**：P2 的「殺掉 sandbox 裡正在跑的指令」——假模型不會呼叫工具，
所以那一半仍然只有單元測試 + 既有的 process-group 測試撐著。

### 仍未做

- **沒有 push、沒開 PR，CI 從頭到尾一次都沒跑過。**
