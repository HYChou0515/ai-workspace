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
| 生成中看得到嗎 | `thinking` 階段有狀態文字但**沒有數字**(那一行只在 `answering` / `toolRunning` 才渲染);tool 執行時 tok/s 與秒數刻意凍結 |

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

§2.1–§2.7 是 `/grill-me` 逐題問出來的結論,不是選項。§2.8 與 §2.9 是實作與 review 過程中
才發現、必須一起定案的兩條——它們推翻了前面的假設,所以寫在這裡而不是留在 commit 訊息裡。

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

### 2.8 顯示與記錄不能共用同一個數字

實作 2.1 時才發現:`_final_tokens` 有**兩個消費者**。

1. `AgentMetrics(phase="final")` 事件 —— 使用者眼睛看的那一行;
2. `turns.py` 直接拿同一組數字組出 `MessageMetrics` 存進資料庫。

所以「沒量到就存 None」**不能靠拿掉 fallback 來達成**。既有測試
`test_final_tokens_prefers_exact_but_falls_back_when_zero_or_absent` 釘住的是真的需求:
拿掉之後畫面會翻成 `↑0 ↓0`,看起來像壞了。它保護的是**顯示**,而 2.1 要求的是**記錄** ——
兩件事,不是同一件。

**做法:給記錄一條自己的路。**

```python
class AgentMetrics:
    phase: Literal["up", "down", "final"]
    prompt_tokens: int = 0            # 顯示用,維持現狀(up/down 是估計,final 是混合)
    completion_tokens: int = 0        # 顯示用
    elapsed_ms: int = 0
    measured_prompt_tokens: int | None = None      # 新增:provider 的真值,沒有就是 None
    measured_completion_tokens: int | None = None  # 新增
    generation_ms: int | None = None               # 新增(2.2 的分母)
    model: str | None = None                       # 新增(2.3)
```

- 顯示欄位**一個字都不動** —— live 那行的行為完全不變,既有測試繼續綠。
- `turns.py` 只把 `measured_*` 寫進 `MessageMetrics`。provider 沒說就是 `None`,
  而不是一個看起來像數字的東西。
- 畫面日後想標「約 500」,資料已經在同一個事件裡,不必再開一條路。

**記錄的寫入是逐欄合併,不是整筆覆寫。**

一開始寫成「只在 `phase == "final"` 時記錄」,理由是 `measured_*` 在 `down` 恆為 `None`,
整筆覆寫會讓記錄在串流中反覆被清空。但那個版本有更糟的後果:`final` **只在串流正常結束後**
才發出,所以 Stop、MaxTurns、provider 錯誤、#113 重複偵測停止的每一輪都拿不到它 —— 而那些
輪次**真的量到了** elapsed 與 generation,卻被一起丟掉,偏偏那正是「它到底跑了多久」最該問
的時候。

所以改成合併:每個 tick 更新它量到的欄位,絕不抹掉它沒量到的。`up` 除外 —— 它帶的
`elapsed_ms=0` 是「一次嘗試開始了」而不是「這輪花了 0 毫秒」,而重試會再發一次。

### 2.9 開口要也沒有用 —— litellm 會替 provider 回答

**這一條被推翻過兩次,兩次都是跑起來才知道的。**

第一版結論是「光是不編造還不夠,得先開口要」:OpenAI 相容端點在串流時不主動回報 usage,
所以不送 `stream_options` 就永遠拿不到真值。於是加上 `ModelSettings(include_usage=True)`。

**對抗式 review 推翻了它。** litellm 在收到這個要求後,會自己補一個 usage:

```python
# litellm/litellm_core_utils/streaming_chunk_builder_utils.py
returned_usage.prompt_tokens = prompt_tokens or token_counter(model=model, messages=messages)
returned_usage.completion_tokens = completion_tokens or token_counter(model=model, text=completion_output)
```

provider 沉默時,那個 `or` 後面的 tokenizer 估計值就會一路變成 `measured_*` 被存下來 ——
**正是 §2.1 禁止的事,而且比沒修之前更糟**(以前是 `None`,現在是一個看起來像量到的數字)。

實測:替身完全不送 usage,記錄下來的是 litellm 的 `7282/80`。而且比對兩份 trace,
有回報與沒回報的 usage **結構完全一樣**(連 `completion_tokens_details.reasoning_tokens` 都
一樣),所以**從資料上分不出真假**。

**定案:不開口要。** token 那一欄在 provider 主動回報之前保持空白。

代價講明白:實務上幾乎沒有 provider 會在串流時主動回報,所以這一欄目前基本上是空的 ——
「Token 量也可以記錄嗎」的答案是「只在 provider 自己說的時候,而我們不會用一個讓 litellm
代答的方式去問」。

**下一步不是猜,是讓知道的人宣告**:per-preset 的 `reports_usage` 開關,預設關,由部署的人
對他清楚會誠實回報的 endpoint(例如 vLLM)打開。那是一個**斷言**而不是啟發式,所以它可以
安全地讓真值進來。追蹤在 #751 ——空白是可以補救的,一段捏造的歷史不行。

---

## 3. Phase

| Phase | 內容 | 狀態 |
|---|---|---|
| **P1** | 記錄不再說謊:`MessageMetrics` 欄位放寬 + 事件加 `measured_*`;寫入是**逐欄合併**(§2.8 —— 一開始寫成「只在 `final` 寫」,那會讓取消的一輪丟掉真的量到的時間) | ✅ |
| **P2** | 生成時間:runner 累計首 token → 末 token 跨往返,事件加 `generation_ms` | ✅ |
| **P3** | model:`FallbackModel` 記下實際服務的 endpoint,事件加 `model`(順帶修好 #69 trace 的謊報) | ✅ |
| **P4** | 前端:`events.ts` 鏡像、tok/s 換分母、thinking / tool 期間都顯示那一行 | ✅ |
| **P5** | 聊天訊息的時間小字 + tooltip(app chat 與 KB chat 共用) | ✅ |
| **P6** | ~~送出 `include_usage`~~ → **撤回**:litellm 會替沉默的 provider 代答一個 tokenizer 估計值,而那會被存成真值(§2.9)。改為不開口要,追蹤在 #751 | ✅ |

每個 phase 都讓事件的新欄位和它的生產者同時落地 —— 先加一個永遠是 `None` 的欄位,
等於在 schema 裡放一個沒人填的洞,而它會被誤讀成「這個 provider 沒回報」。

每個 phase 一個 commit,走 red-green-refactor。
