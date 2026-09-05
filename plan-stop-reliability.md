# Stop 的可靠性

使用者回報兩件事，查下來是同一個區域的不同缺陷：

1. **「按下 Stop 之後要過好久才會真的停下。」**
2. **「訊息欄裡有東西時按 Stop，會把當前停下然後送出——但使用者只覺得東西不見了，AI 還在跑，像系統故障。」**

第二件的一半（「東西不見了」）已經修掉：送出時本地就畫出自己的訊息，不再等後端廣播。
見 commit `17e82862` + `67da5148`（真瀏覽器量測：注入 5 秒後端延遲下，泡泡從 2823ms → 34ms，
且廣播到達後仍只有一顆）。本計畫處理其餘部分。

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
