# #739 — 對話塞不下時把它壓成摘要,而不是叫使用者開新對話

**問題一句話**:長對話走到 context 上限一定會斷,而系統對「你講太久了」的唯一答案是**要使用者自己重來一次**。

[#624](plan-issue-624.md) 解決了「不知道模型能吃多少」。這一條解決接下來那個問題:**知道了以後要怎麼辦。**

---

## 1. 現狀

### 1.1 唯一的處理方式是丟掉

`context_reducers.LayeredReducer` 的三個階段:折疊肥大的 tool output → 丟掉中段 → 最後連使用者最初交代的任務都丟。丟完在對話裡插一則 notice:

> 「……需要它記得那些內容的話,請開一個新對話。」

成本整個落在使用者身上:背景要重講、檔案路徑要重貼、已經試過哪些死路要重新交代。

### 1.2 出口早就寫好了,只是沒人走

`context_reduce.ReductionResult.summary` 的註解自己寫著:

> *"a summarising one would replace a span with a précis."*

那個實作從來沒有存在過。`docs/plan-item-memory.md` §④ 也把界線劃清楚:context editing 是**刪掉**,compaction 是**摘要**,兩件不同的事。我們只有前者。

### 1.3 畫面上那個 token 數字是假的

`litellm_runner` 的 `phase="up"` 是 `_approx_tokens(len(prompt))` —— **只算使用者這一則訊息的長度**,跟 context 大小無關,也不會隨著視窗變滿而移動。而 `phase="final"` 用的是 provider 回報的**整個請求**的量。同一個數字在同一輪內換了兩種意思,差一個數量級。

所以使用者看不到自己快滿了,第一個徵兆就是對話被截斷。

---

## 2. 定案的設計

以下六條是 `/grill-me` 逐題問出來的結論,不是選項。

### 2.1 自動 + 手動都要,而且是同一個機制

兩者都只是**寫一則摘要訊息**,差別只在誰按下去。不做兩條路 —— 兩套並存保證其中一套會變假。

### 2.2 用量 = 上一輪的真值 + 本輪新增訊息的估計

真值只有一輪跑完才有(`AgentMetrics(phase="final")` 帶的 `usage.input_tokens`,已持久化在 `Message.metrics`),但那正好是下一輪的基準。誤差因此只剩**最後一輪的增量**,而不是整段歷史累積的估計誤差。

真值還含了估計值從來看不到的固定開銷:system prompt、每個 tool 的 JSON schema、skills 索引。

錨點訊息**自己那則回答**要用它的 `completion_tokens` 補上 —— `prompt_tokens` 只算輸入,那則回答不在它量到的視窗裡,但會在下一個視窗裡。

### 2.3 原文不刪

`history_items` 本來就是一個**純視圖**:每輪從完整的持久化訊息重新組一次給模型,從不動存起來的東西。所以壓縮寫入的是一則 `role="summary"` 的訊息,`history_items` 改成從**最新的那一則**開始重播:`[摘要] + [它之後的訊息]`。

使用者捲上去,原文全都在。

**不需要 specstar 遷移**:`Conversation.messages` 是內嵌 list、`Message.role` 是自由字串又沒有索引,切點由訊息順序決定,不必存成欄位。

### 2.4 不給 agent `compact` 工具

入口只有自動門檻和使用者手動。

理由:agent 要自己決定何時壓,前提是每輪 prompt 都得告訴它「你還剩 N tokens」—— 那是一整條新的 prompt 面,而且會讓模型拿 context 當偷懶的藉口。本地小模型多一個不懂何時該用的工具,結果不是亂用就是永遠不用(#618 那次 `num_ctx` 截斷讓模型直接謊稱工具不存在,同一類風險)。

agent 唯一能貢獻的判斷是「這段以後用不到」—— 而那正是摘要本身在做的事。

### 2.5 使用者入口:`/compact` 加一顆按鈕,同一條路由

slash command 是熟手手勢(Claude Code / Slack / Discord 都是),但它**天生不可發現**,所以 web UI 需要一顆看得到的東西。兩者呼叫同一個 endpoint,不分岔。

一顆 compact 專用按鈕才是特例;通用手勢只有一個成員不是過度設計。

### 2.6 摘要由丟棄式子代理寫,用這個對話當下的 model

這是 `ask_knowledge_base` 的形狀(#270):吵的留在子代理的拋棄式 context,只有結論回來。被壓縮的那段是全系統最吵的輸入 —— 它**正是因為塞不下才要被壓** —— 讀回呼叫端的 context 去摘要,等於為了省 context 先炸一次 context。

model 不另配便宜端點。聊天用大模型、摘要偷用小模型,退化的方式是**沒人看得見的**:沒有錯誤、日誌裡沒有東西可指,只會覺得「它後來變笨了」。

---

## 3. 幾個要守住的

- **門檻不是一個新旋鈕。** 觸發點就是現在 reducer 會開始丟東西的那一刻。順序是 **折疊 → 壓縮 → 丟棄**:折疊 tool output 是免費的,先做;折完還是塞不下才花一次 LLM;壓縮失敗才退回丟棄。**丟棄從此是退路,不是常態。**

- **摘要必須逐字保留四類東西**,否則壓完等於白壓:

  1. 使用者**最初**交代的任務(原話,不要改寫 —— `LayeredReducer` 的教訓就是這個最先被丟掉)
  2. 已經做完的事和結論
  3. **未完成**的事、還沒驗證的假設、待辦
  4. 檔案路徑、id、指令、錯誤訊息 —— **逐字**

  被壓掉的 tool output 不照抄,只留結論。

- **上限未知時不要編一個分母。** `history_budget` 回 `None` 的意思是「不知道上限」,不是「用預設值」。UI 只顯示已用量。硬編假分母正是 #624 抓到的病:一個沒人量過、大家都相信的數字。

- **壓縮那一輪會明顯變慢**(多一次 LLM 往返),畫面必須看得到「正在壓縮」。委派最怕的就是畫面像凍住(#738 同一條)。

- **UI 文案不講內部名詞。** 使用者該看到的是「它還記得」,不是 estimate / reducer / budget。

---

## 4. Phase

| Phase | 內容 | 狀態 |
|---|---|---|
| **P1** | `context_usage` —— 錨定 provider 回報的真值,算出這串現在佔多少 | ✅ `c9fee69a` |
| **P2** | `GET .../chats/{id}/context` + `ContextBar` 上畫面;修掉假的 live ↑ | ✅ `99bb4991` |
| **P3** | 壓縮核心:`AgentCompactor`、`split_for_compaction`、history 與量表都從摘要起算 | ✅ `ad2bc570` |
| **P4** | 自動觸發 | ⬜ |
| **P5** | 手動觸發 | ⬜ |
| **P6** | 對話裡的壓縮卡片 | ⬜ |

### P1 — 這串現在佔多少(已完成)

`context_budget.context_usage(messages, *, limit) -> ContextUsage`。錨點是最後一則帶 `metrics.prompt_tokens > 0` 的 assistant 訊息。

拒絕算進去或編出來的三件事:

- `notice` 與失敗的 turn 都不進模型(`history_items` 依 kind 擋掉),算它們會讓量表自己長高。取消例外 —— 它會以折疊標記的形式重播(#199)。
- 回報 `0` 是**沒有量到**,不是量到零。錨在它上面,會在某個 provider 安靜下來的瞬間把視窗清空。
- 上限未知 ⇒ `ratio` 是 `None`。

### P2 — 把真的數字放上畫面(已完成)

- `GET /a/{slug}/items/{item_id}/chats/{chat_id}/context` → `{used, limit, measured}`。形狀比照 `/todos`:初次 hydration 走 GET,即時更新走 stream。
- 視窗由 `TurnContextBuilder.usage_of` 解析,**不在路由重算** —— 路由自己算會跟 turn 實際編列的預算漂移,那正是 #624 的病。
- `_live_prompt_tokens` 讓進行中的 ↑ 和沉默截斷偵測共用同一條算式。
- `ContextBar` 掛在儲存空間量表旁邊;每輪結束 invalidate。**一個不會動的量表比沒有量表更糟,因為人會相信它。**

### P3 — 壓縮核心(已完成)

- `history_items` 從最新 `SUMMARY_ROLE` 起重播。切點在**數量與 token 兩道窗之前**,否則壓縮會繼續為它剛騰出來的 token 付錢。
- 摘要以 `user` item 重播 —— `system` 放在對話中間會被 provider 直接拒絕(#199 繞開的同一個限制)。
- `context_usage` 也切在同一點,並退回 `measured=False`:上一輪回報的數字算的是一個還包含被替換那段的請求,它已經不是「視窗現在多滿」的答案。
- `split_for_compaction` 永不動最近幾則,也永不跨過上一個摘要(摘要的摘要 = 複製的複製)。
- `SUMMARY_ROLE` 只定義在 `context_budget` 一處,API 匯入、絕不反向。

### P4 — 自動觸發(待做)

插入點是 `chat_send._send`:**附加使用者訊息之後、建 turn context 之前** —— 那裡這一串已經定案,而且 `_send` 本來就跑在 shielded 背景 task 裡,所以慢的壓縮不會卡住 HTTP 請求。

- 判斷交給 `TurnContextBuilder` 的新公開方法(window 只在那裡解析)。
- 摘要 **insert 到切點**,不是 append —— 它必須在保留的近期訊息**之前**。
- 順序 折疊 → 壓縮 → 丟棄;壓縮回空字串就不壓(用空的取代一段,比截斷更糟)。
- 串流吐出「正在壓縮」,參考 #492 的 `RestoreProgress`。

### P5 — 手動觸發(待做)

`POST /a/{slug}/items/{item_id}/chats/{chat_id}/compact`,呼叫 P4 的**同一個函式**。

composer 認得 `/` 開頭、不當文字送出;旁邊一顆看得見的按鈕。使用者打的那行 `/compact` **不進 LLM history、也不存成 user 訊息** —— 進去的只有它產生的摘要。

### P6 — 對話裡的呈現(待做)

`role="summary"` 渲染成可展開的卡片(摘要 +「已壓縮 N 則訊息」),原文留在它上面照常顯示。自動和手動長得一模一樣,只有 caller 不同。

⚠️ 前端 `agentLog` / `AgentEntryView` 要**先查**現在遇到不認得的角色會怎樣,不能假設它安靜地不顯示。

---

## 5. 已知偏離

P3 原計畫含「持久化摘要」。實作時發現那屬於**決定何時壓縮的呼叫端**,不屬於核心 —— 所以落到 P4/P5。P3 出的是純核心,全部不需要真模型就能測。

---

## 6. 風險

- **摘要品質只能靠真模型驗。** 這台開發機的 ollama 純 CPU,跑不完一個真實 turn。機制可以被測試蓋住(切點、重播、路由、卡片),「摘得好不好」不行。跟 #738 是同一條誠實界線 —— 要眼見為憑需要一台 GPU 真的有作用的機器。
- **壓縮那一輪多一次 LLM 往返**,體感明顯變慢。所以 P4 的「正在壓縮」不是裝飾。

---

## 7. 驗收

- targeted 測試 + `ruff` + `ty` 綠(全套是 CI 的事,見 CLAUDE.md `## Workflow`)
- **每個守衛都有必紅的突變探針**,而且對照組必綠 —— P1 五個、P3 六個,已完成
- CI(PR #746)全綠
- **自己把 app 跑起來按過** —— merged 但對使用者隱形等於沒完成
