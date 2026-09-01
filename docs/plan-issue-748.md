# #748 — 一則回答是誰、什麼時候、花多少

**問題一句話**:一則回答產生出來之後,關於它是怎麼產生的我們幾乎什麼都沒留下 —— 而留下的那一點還是錯的。

[#624](plan-issue-624.md) 解決了「不知道模型能吃多少」,#739 解決了「滿了怎麼辦」。這一條解決的是更前面的問題:**這則回答本身的來歷**。

---

## 1. 現狀

| 事實 | 現狀 |
|---|---|
| 什麼時候回的 | `Message.created_at` **早就存了**,前端每個 entry 也已經帶著 `at` —— 只是聊天訊息從沒把它畫出來 |
| 哪個 model 回的 | **從來沒記錄過** |
| 用了多少 token | 有記,但真值和 `chars/4` 的估計值**混在同一個欄位**,記錄本身分不出來 |
| 生成多快 | 分母是**整輪牆鐘**,含 TTFT、tool 執行、重試、rate-limit 等待 |
| 生成中看得到嗎 | `thinking` 階段整行不渲染;tool 執行時刻意凍結 |

### 1.1 `_final_tokens` 靜默地退回估計值

```python
if usage is None:
    return prompt_tok, approx_completion              # 純估計 (chars/4)
return (usage[0] or prompt_tok, usage[1] or approx_completion)  # 逐欄退回
```

註解自承原因:「Ollama often streams usage as 0 — otherwise the final line would flip to ↑0 ↓0」。

當畫面裝飾,這是合理的取捨;當**記錄**,它是一個沒有標記的假數字。差別在於記錄會被拿去做判斷 —— 比較模型、算成本、抓異常 —— 而那些判斷全部會無聲地錯。

### 1.2 #69 的 trace 在 failover 時報錯 model

```python
model = getattr(getattr(agent, "model", None), "model", "") or (cfg.model if cfg else "")
```

`FallbackModel` 沒有 `.model`,也不記錄實際服務的 endpoint,所以這行會**靜默退回設定值**。也就是:**在 model 最可能不同的那一刻,報的正好是錯的。**

### 1.3 tok/s 量的不是生成速度

```ts
m.completionTokens / (m.elapsedMs / 1000)
```

`elapsedMs` 從 `t0 = time.monotonic()`(整輪開始)算起。一輪裡有個跑 60 秒的 `exec`,tok/s 就低一個數量級 —— 它量的是「這輪整體多快」,而那個已經用 `· 12.3s` 顯示過了。

---

## 2. 定案的設計

以下七條是 `/grill-me` 逐題問出來的結論,不是選項。

### 2.1 沒量到就是沒量到

provider 沒回報真實 token(本機 Ollama 常回 0)時,存 `None`,**絕不存估計值**。

「不知道」必須長得像不知道。一個分不出真假的數字,在任何用途上都不能用,而且錯得無聲。畫面上要顯示約略值是另一回事 —— **存下來的那筆不能有假貨**。

### 2.2 tok/s 是生成速度,而且不含 TTFT

分母只算**首 token → 末 token**,跨多次 LLM 往返累加。

排除 TTFT 的理由:TTFT 是處理 prompt 和排隊,不是生成。含進去的話,**prompt 越長同一個模型看起來越慢** —— 正是最容易誤判的情境(對話變長會被讀成模型變慢)。llama.cpp / vLLM 的 benchmark 也是把 prompt eval 與 generation 分開報。

### 2.3 記「寫出這則文字」那次呼叫的 model

一輪可能有多次往返,failover 可能在中間換人。`Message` 裝的就是那段文字,所以記最後一次成功呼叫用的 model。前面幾次換過人這件事由 `FailoverSwitch` 事件負責,不塞進這個欄位。

不記完整清單(`["A","A","B"]`)——那是 trace 的職責,而 tooltip 塞不下也不該塞。

### 2.4 那一行在每個階段都可見、會動

thinking 時要顯示(後端本來就在推,`completion_chars` 連 reasoning delta 都算);tool 執行時也不要拿掉。

**2.2 讓 tool 期間的凍結不再必要**:原本要凍結,是因為分母含 tool 時間、tok/s 會邊跑邊衰減成假值;改成生成時間之後,它只是停在最後一個正確值 —— 沒有新的生成,速度就還是那個速度。

### 2.5 一個家,兩個時間欄位

全部放進 `MessageMetrics`,並把它的定位從「token 用量」正名為「這則回答是怎麼產生的」:

```python
class MessageMetrics(Struct, frozen=True):
    model: str | None = None             # 新增
    prompt_tokens: int | None = None     # int → int | None
    completion_tokens: int | None = None # int → int | None
    elapsed_ms: int = 0                  # 不變:整輪牆鐘,畫面上的「· 12.3s」
    generation_ms: int | None = None     # 新增:tok/s 的分母
```

- `prompt_tokens` **反正**要因 2.1 變成可空,所以 model 放進來是同一件事,不是多開一條路。
- 拆成 `Message.model` + `Message.metrics` 兩個家,總有一天有人只更新一邊。
- `elapsed_ms` 與 `generation_ms` **必須是兩欄**。這正是 #739 §1.3 記下的錯:同一個數字在同一輪內換兩種意思。

**不需要遷移**:`Conversation.messages` 是內嵌 list,`MessageMetrics` 沒有任何索引,所以不走 specstar 的 `migrate` 路(那是給 indexed 欄位用的)。舊資料照原樣解得開,新欄位吃 `None`,前端就不顯示 —— 與 `created_at` 既有的契約一致。

### 2.6 UI:外面只有時間,其餘全進 tooltip

```
                                    14:32 ▲   ← 灰字,永遠可見
                                       │
              ┌────────────────────────┘
              │ 2026-09-01 14:32:07
              │ qwen3:14b
              │ ↑ 8,412 · ↓ 356 tok · 47 tok/s
```

- 可見的只有 `14:32`(分鐘精度;秒進 tooltip)。
- model / tokens / tok/s **全部只在 tooltip** —— user 明確要求「別太明顯」,所以一個都不放外面,而且共用同一個 hover 目標,不多長圖示。
- 使用者自己的訊息也顯示時間(只有一邊有時間戳的對話很怪,資料本來就在),tooltip 不含 model —— 那是人寫的。
- tooltip 用原生 `title=`,聊天區既有做法(`AgentEntryView` 的 replay / undo 都是),不引入新元件。
- ⚠️ **tooltip 在觸控裝置上等於不存在。** 桌面優先是知情的取捨;真要支援觸控,model 得改成外層灰字,那會和「別太明顯」衝突,屆時要重新拍板。

### 2.7 邊界:`↑` 不歸這裡管

`phase="up"` 的 `prompt_tokens` 只算使用者那一則訊息的長度,和整個請求差一個數量級 —— 那是 #739 §1.3 的題目,`context_usage` 正在錨定真值。**本 issue 不碰 ↑**,只處理 ↓、tok/s、model、時間。

兩邊都改同一個地方會漂移。

---

## 3. Phase

| Phase | 內容 | 狀態 |
|---|---|---|
| **P1** | `MessageMetrics` 加 `model` / `generation_ms`、tokens 放寬為可空;`_final_tokens` 不再編造估計值 | ⬜ |
| **P2** | 在 runner 累計生成時間(首 token → 末 token,跨往返累加) | ⬜ |
| **P3** | `FallbackModel` 記下實際服務的 endpoint;runner 讀它(順帶修好 #69 trace 的謊報) | ⬜ |
| **P4** | `AgentMetrics` 事件帶 model + generation_ms;FE 鏡像、reducer、tok/s 換分母、每個階段都顯示 | ⬜ |
| **P5** | 聊天訊息的時間小字 + tooltip(app chat 與 KB chat 共用) | ⬜ |

每個 phase 一個 commit,走 red-green-refactor。
