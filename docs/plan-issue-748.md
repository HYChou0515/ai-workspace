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
    model: str | None = None                 # 寫出這則文字的那個
    prompt_tokens: int = 0                   # 導航用:永遠有值,可能是估計
    completion_tokens: int = 0               # 導航用
    measured_prompt_tokens: int | None = None    # 採信用:provider 的真值,沒有就 None
    measured_completion_tokens: int | None = None
    elapsed_ms: int = 0                      # 整輪牆鐘,畫面上的「· 12.3s」
    generation_ms: int | None = None         # tok/s 的分母
    exact: bool = False                      # #739 的旗標,由 measured_prompt 導出
```

⚠️ **「導航」和「採信」必須是不同欄位,這一點我做錯過一次。** 一開始只留可空的那一組,
理由是「沒量到就該長得像沒量到」—— 但 #739 的 context 量表**錨在 `prompt_tokens > 0`**,
而它在自己的世界裡永遠是 int(provider 沉默時由 turn 塞估計值)。改成可空之後,量表在
**預設設定下永遠找不到錨**,退回它自己量測並否決的那條路(「+500 誤差變成 −5,800」、
「壓縮觸發不再發火」)。

§2.8 講的正是這件事,而我只把它做在事件上、在持久化的 struct 裡合了回去:
**量表要的是「就算是估計也要有數字」,記錄要的是「不能有分不出真假的數字」。**

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

### 2.9 開口要不安全,除非有人替那個 endpoint 背書

**這一條被推翻兩次,兩次都是跑起來才知道的,而第三次是使用者提供的真實資料。**

第一版:「光是不編造還不夠,得先開口要」—— OpenAI 相容端點在串流時不主動回報 usage,
所以加上 `include_usage`。

**第二版(對抗式 review 推翻):** litellm 收到這個要求後會自己補一個 usage:

```python
# litellm/litellm_core_utils/streaming_chunk_builder_utils.py
returned_usage.prompt_tokens = prompt_tokens or token_counter(model=model, messages=messages)
```

provider 沉默時,`or` 後面的 tokenizer 估計值就變成 `measured_*` 被存下來 —— 正是 §2.1 禁止
的事,而且比沒修之前更糟。實測:替身完全不送 usage,存進去的是 litellm 的 `7282/80`;而且
有回報與沒回報的 usage **結構完全一樣**,從資料上分不出真假。所以改成「不開口要」。

**第三版(定案,依真實部署的量測):** 「不開口要」誠實但把這一欄變成永遠空的。使用者對
**他們自己的 litellm proxy** 量了三次:

| 請求 | prompt / completion |
|---|---|
| 非串流(後端真值,不經過會代答的組裝器) | 69 / 16 |
| 串流 + `include_usage` | **69 / 16** ← 一致 |
| 串流、不送 `include_usage` | **完全沒有 usage** |

也就是**要問才給,而給的是真的**。本機 Ollama 則怎樣都不給、由 litellm 代答。

所以這是 **endpoint 的性質,而且從回覆上看不出來** —— 兩者產生的物件形狀一模一樣。於是它
只能是**宣告**:`agents.presets.<name>.reports_usage`,和 `vision` 同一類(同樣因為無法從
回覆偵測而必須宣告)。**預設關**,因為錯誤的 `true` 會把捏造值寫進歷史當成量測,錯誤的
`false` 只是留白 —— 只有後者可以事後補救。

⚠️ **判定程序需要三個 curl,不是兩個。** 「非串流 ≈ 串流」看似足夠,但**沉默的 endpoint 會
讓兩邊都被 litellm 代答**,於是它們一致 —— 一致在同一個捏造的數字上。第三個(串流、不問)
才是分辨點:它區分「本來就沉默,只是有人幫忙填了」和「問了才給」。程序寫在
`configs/config.example.yaml`。

⚠️ **litellm 即使透傳真值,仍會往那個物件塞自己算的欄位。** 這個部署回了
`reasoning_tokens: 39` 對 `completion_tokens: 16` —— 兩者不可能同時為真。我們只讀
`prompt_tokens` / `completion_tokens`;任何要用 details 的人不能假設那是 provider 給的。

### 2.10 「要不要算」和「要不要顯示」是兩個問題

實作 2.2 時把 tool-call 參數的 delta 納入生成時間(provider 確實把它算進 completion
token),做法是把判準從 `channel != "ignore"` 換成 `_is_generated_output(...)`。

**但那個 `if` 同時管著文字路由** —— 而 `"ignore"` 不等於 `"reasoning"`,所以參數 JSON 掉進
`else`(內容)分支,被當成回覆文字送出去:

```
VISIBLE ANSWER TEXT = 'Hello. {"path":"/etc/passwd","limit":50}'
```

**兩個判準各自都有測試、各自都過** —— 它們只有在**合起來**的時候才是錯的,而沒有人測合起來。
路由現在明確寫 `elif channel == "content"`,計數留在白名單上,並補了一個真的驅動串流迴圈的
測試(stub `Runner.run_streamed`)。

教訓寫在這裡而不是留在 commit 裡:**一個 `if` 若同時回答兩個問題,換掉它的條件時必須兩個
問題都重新問一次。**

### 2.11 宣告要能真的抵達,而且不能被 failover 繞過

`reports_usage` 第一版是死的:`_build_preset` 是手寫的 kwargs 清單,而嚴格驗證**會接受**這個
key(它確實是 Preset 欄位),所以 operator 設了沒反應、也沒有錯誤。三個 builder 都要學會它
(`loader._build_preset`、`schema._preset_from_dict`、`catalog_build.resolve_usage` —— 最後
那個是 app chat 以外**每一個** preset-referencing 角色)。

**而測試之所以綠,是因為它直接建 `AgentConfig(reports_usage=True)`,繞過整條鏈。** 驗證必須
從真入口進去:真的 loader、真的 `resolve_usage`。

**failover 是它唯一還能說謊的地方。** 整條 chain 共用一份 `ModelSettings`,所以已宣告的 head
會把 `include_usage` 交給它切過去的 endpoint。已宣告的 head 搭配未宣告的 fallback 在**載入時
就拒絕**(和其他 preset 驗證放在一起),而不是留到幾週後才在記錄裡發現。

**只有串流路徑受這個旗標影響。** 非串流的回應把 provider 的 usage 直接透傳、沒有代換,所以
`WORKSPACE_AGENT_STREAM=0` 不論有沒有宣告都記錄真值。

---

## 3. Phase

| Phase | 內容 | 狀態 |
|---|---|---|
| **P1** | 記錄不再說謊:`MessageMetrics` 欄位放寬 + 事件加 `measured_*`;寫入是**逐欄合併**(§2.8 —— 一開始寫成「只在 `final` 寫」,那會讓取消的一輪丟掉真的量到的時間) | ✅ |
| **P2** | 生成時間:runner 累計首 token → 末 token 跨往返,事件加 `generation_ms` | ✅ |
| **P3** | model:`FallbackModel` 記下實際服務的 endpoint,事件加 `model`(順帶修好 #69 trace 的謊報) | ✅ |
| **P4** | 前端:`events.ts` 鏡像、tok/s 換分母、thinking / tool 期間都顯示那一行 | ✅ |
| **P5** | 聊天訊息的時間小字 + tooltip(app chat 與 KB chat 共用) | ✅ |
| **P6** | ~~全域送出 `include_usage`~~ → 撤回(§2.9 第二版):litellm 會替沉默的 provider 代答 | ✅ |
| **P7** | Review round 1:紅燈測試、KB chat 缺欄位、tok/s 分子分母錯配、取消的一輪丟失 metrics | ✅ |
| **P8** | Review round 2:對話進行中沒有時間戳(改讀 `entry.at`)、`_effective_model` 存空字串 | ✅ |
| **P9** | Review round 3(regression lens):tok/s 錯配「搬家」成低估、`up` tick 歸零已量到的時間、時鐘仍可回 `0`、thinking 行印出兩個矛盾秒數 | ✅ |
| **P10** | 重跑突變 probe,補上只有一道防線守著的那半 | ✅ |
| **P11** | 不開口要 usage(§2.9 第二版的落地) | ✅ |
| **P12** | **tool-call JSON 漏進回覆內文**(§2.10)—— P9 造成,並補上真的驅動串流迴圈的測試 | ✅ |
| **P13** | per-preset `reports_usage`(§2.9 定案,依真實部署量測);#751 關閉 | ✅ |
| **P14** | 旋鈕原本是死的、且 failover 可繞過(§2.11) | ✅ |
| **P15** | role 不得替 endpoint 背書(usage 區塊寫了就在載入時拒絕) | ✅ |
| **P16** | app chat 的接線沒有測試 —— 主路徑,同一個死旋鈕形狀 | ✅ |
| — | 合併 master(#739 compaction);兩套「這數字真不真」的機制合一,`exact` 改為導出 | ✅ |
| **P17** | 記錄吞掉了量表的錨點(§2.5)—— §2.8 只做在事件上、沒做在 struct 上 | ✅ |
| **P18** | 只有 token 是估計時仍顯示 `≈ N tok/s`;兩處過期 docstring;`_USAGE_FIELDS` 的接受集對齊拒絕集 | ✅ |

---


每個 phase 都讓事件的新欄位和它的生產者同時落地 —— 先加一個永遠是 `None` 的欄位,
等於在 schema 裡放一個沒人填的洞,而它會被誤讀成「這個 provider 沒回報」。
每個 phase 一個 commit,走 red-green-refactor。

---

## 4. 這輪對抗式 review 改了什麼

`/review-loop`,四個 lens 平行跑 + 突變 probe,共五輪。**最嚴重的缺陷幾乎全是這個 PR 自己
造成的,而且有兩個是修正引入的** —— 值得記下來,因為它們是同一類錯誤的不同面貌。

| 缺陷 | 怎麼跑掉的 |
|---|---|
| 一條測試從 P1 起就是紅的,而我回報成綠 | 那輪 `pytest` 因 `--timeout` 不存在**根本沒跑**,我只看了 exit code |
| tok/s 先被修成**高估**、再被修成**低估** | 分子分母是兩個不同的母體,第一次只改了其中一邊 |
| tool-call JSON 印進回覆 | 兩個判準各自有測試、合起來才錯,而沒有人測合起來(§2.10) |
| `reports_usage` 完全無效 | 測試直接建 `AgentConfig`,繞過整條鏈(§2.11) |
| 取消的一輪丟失 metrics | 只想著「串流正常結束」那條路 |

**三條可以帶走的判準:**

1. **修正是新程式碼,而且是缺陷最密集的地方。** 五輪裡有兩輪的最嚴重項是前一輪的修正造成的。
2. **斷言要從真入口進去。** 直接建 `AgentConfig`、直接呼叫判準函式,都會在整條鏈斷掉時保持綠燈。
3. **沒有 probe 的保證只是願望** —— 而**探針本身也會壞**:一個只替換三處相同分支中第一處的
   突變,會給出假的 WISH,差點被我當成產品缺陷回報。
