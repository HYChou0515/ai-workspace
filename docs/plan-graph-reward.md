# Plan — 把抽取調校迴圈的 reward 做對(#697 後續)

## 這份計畫要解的問題

`--tune-round` 現在的 reward 只有五個數字(每篇幾個、不重複幾個、數字開頭幾個、kinds、60 個抽樣名字)。

**這五個數字全部朝同一個方向:抽越少越好看。** 抽出零個在每一項上都滿分,而那是最糟的結果。meta-prompt 目前靠一句「WORST possible outcome」的告誡擋著,那是提醒,不是訊號 —— 迴圈本身量不到自己收得太緊。

文獻上這叫 reward hacking;迴圈本身則是 OPRO / ProTeGi 那一系(見最後的〈文獻〉)。

## 已定案(對話中拍板,含被推翻的東西)

| 決定 | 理由 |
|---|---|
| **不做黑名單 / `must_not`** | 排除項是**不可逆的吸收態**:一個詞被排除 → 不再被抽出 → 不再出現在 `kept` → **後面沒有任何一輪能發現這個決定是錯的**。而且它只教模型避開那幾個字,不教它避開那個類別,換一批文件就原形畢露。 |
| **不做 `must_find` 人工名單** | 兩個理由。(a) 在 mini-batch 上量 recall,只有**近乎每篇都出現**的詞量得穩定 —— 那正是我們要殺的文件家具;df=1/8 的詞每輪不是 0 就是 1,那是取樣解析度不足,不是 prompt 變差。(b) 更根本:列得出那份名單,就代表已經知道答案了。 |
| **df 一律從整個 pool 的原文算** | 八篇估不出 1/8 的詞頻。batch 只該決定「這輪花多少模型呼叫」,不該決定「統計量從多少樣本估」。數幾個 `.txt` 含有某字串是純字串比對,不用模型。 |
| **罰則要兩維都低才成立** | df 單獨一維分不出「罕見但真實」與「一次性垃圾」。 |
| **低 df + 高文內次數要加分** | 那是最有鑑別度的形狀(只在回焊那節出現的機台)。 |
| **下游指標是 lookup 命中率,不是 recall@k** | graph 沒有餵進向量檢索;它被 `lookup_entity(name)` 以正規化後的名字**精確查表**取用(`kb/graph/lookup.py`)。 |

### 四象限 —— 判斷「是不是東西」要兩個維度

|                     | 文內出現多次     | 文內只出現一次           |
| ------------------- | ---------------- | ------------------------ |
| **很多篇都有**      | 核心主題 ✅       | **文件家具**(訊息、討論)❌ |
| **只有少數篇有**    | **最有鑑別度** ✅ | 一次性垃圾(值、路過的名詞)❌ |

## 三層 reward

便宜的代理指標負責爬,昂貴的真指標定期校準 —— 這是標準做法,不是新東西。

| 層 | 訊號 | 成本 | 節奏 |
|---|---|---|---|
| 快 | ATR 統計(四象限、weirdness) | 零模型呼叫 | 每輪 |
| 中 | LLM-as-judge 抽樣 + 流失名單 | 少量呼叫 | 每輪 |
| 慢 | **lookup 命中率**(下游真指標) | 一組探針 | 每 N 輪(跟 holdout 同節奏) |

前兩層讓迴圈跑得動,第三層負責**確認前兩層沒在說謊**。

---

## Phases

### P1 — 下游 reward:lookup 命中率

**為什麼先做這個**:沒有它,後面每一層改得再漂亮都沒人能證明有用。

一次性從 **holdout 的原文**產生一組**凍結的探針**:每篇問模型「一個讀者事後會想回來查的東西,列 3 個」,存成 `rounds/probes.json`。然後每次評估時檢查:圖裡查不查得到這些名字。

- 探針**產生一次就凍結**,不隨版本重跑 —— 否則靶會跟著移動
- 探針是人可讀的純文字檔,擁有者可以刪掉不合理的(選配,不做也能跑)
- 比對走 `norm_surface`,跟 `lookup_entity` 的查表規則同一套 —— 量的是真的查得到,不是「大概有」
- **抽零個 → 命中率 0%**,這隻腳擋的就是這個

新增 scorecard 欄位:`lookup_hit_rate`、`probes_total`、`probes_missed`(附名字,不只數字)。

> 探針由模型產生,跟抽取器同一個家族,所以帶有共同偏誤。用得下去的理由是那個真實的不對稱:**判斷比生成簡單** —— 「列 3 個讀者會想查的東西」比「列出所有重要的東西」窄得多。這是 LLM-as-judge 能成立的同一個理由,不是無條件可信。

### P2 — ATR:df 從 pool 算 + 四象限

- `document_frequency(pool)`:掃 `rounds/tune/*.txt` 的**原文**,純字串比對,與 batch 大小脫鉤
- 每個抽出的名字落到四象限的哪一格
- scorecard 多三行:`furniture`(高 df 低 tf 的占比)、`singleton`(低 df 低 tf 的占比)、`discriminative`(低 df 高 tf 的占比,**越高越好**)
- 送進 meta-prompt 時附上每一格的**實際名字**,不只是比例 —— 比例是可以靠抽零個做漂亮的,名字不行

### P3 — 流失名單(recall 的第二隻腳)

在 **holdout** 上算 `v_{n-1}` 有而 `v_n` 沒有的名字。holdout 每次都是同一批文件,所以名字消失可歸因到 prompt,不是抽籤抽掉的。

送進 meta-prompt 時附**文內出現次數 + 原文引句**:

```
你這一版丟掉了這些名字,確定嗎:
  回焊爐   (在 d3 出現 14 次)  "...回焊爐溫度設定為..."
  訊息     (在 d3 出現 1 次)   "## 訊息"
```

判準沿用 P2 的兩維:丟掉高度聚合的詞可疑,丟掉 singleton 是好事。

### P4 — meta-prompt 帶前幾版 prompt 全文

現在 history 只送 `version / per_doc / digits` 兩個數字,**過去版本的 prompt 全文沒送** —— 所以模型會來回震盪:改壞、改回來、再改壞。把最近 K 版的 prompt 連同分數一起送。最便宜的一個修法。

### P5 — beam:保留 top-k 而非單線

現在是單線爬山 `v_n → v_{n+1}`,舊版躺在磁碟上但不參與後續。ProTeGi 的做法是保留 top-4 一起往下走。改成從**歷史最佳的 k 版**各衍生一個候選,下一輪擇優。

### P6 — LLM-as-judge 抽樣

對 `kept` 抽樣逐個問「人能不能指著它或查到它」。放在 P1 之後,因為要有下游指標才能驗證這個評審跟真實效用有沒有相關。

### P7 — weirdness ratio(待拍板)

領域語料 vs 一般語料的頻率比:「訊息」「討論」在兩邊都高頻 → 自動出局,不用黑名單、不用打字、**而且會類推**。

**待拍板:對照語料從哪來。** 候選:(a) 內建一份通用中文/英文詞頻表,(b) 拿使用者其他 collection 當對照,(c) 不做,用 P2 的 df 當退化版。

---

## 非目標

- 不動模型權重。整個東西是 in-context 的優化,不是參數的優化。真 RL 需要 LoRA + 一份標註過的偏好資料集,而**沒有那份資料集就無從開始** —— P1/P6 的產出正好是它的種子。
- 不做黑名單、不做人工 `must_find`(理由見上)。

## 文獻

迴圈本身是既有技術,照抄即可;新的只是把 ATR 統計量接成無標註條件下的 reward。

- **OPRO** — Yang et al., *Large Language Models as Optimizers*, arXiv 2309.03409。meta-prompt 帶 (prompt, 分數) 軌跡,即 `index.json` → revise。
- **ProTeGi / APO** — Pryzant et al., *Automatic Prompt Optimization with "Gradient Descent" and Beam Search*, arXiv 2305.03495 (EMNLP'23)。mini-batch + 文字梯度 + beam search;**論文自己記錄了第 3~4 輪 overfit 到特定 minibatch**,解法是 beam + bandit 選擇 → P5。
- **Reflexion** — Shinn et al., arXiv 2303.11366。自稱 *verbal reinforcement learning*,即「借用 RL 概念但不動權重」。
- **Self-Refine** — Madaan et al., arXiv 2303.17651。
- **LLM-as-judge** — Zheng et al., arXiv 2306.05685 → P6。
- **C-value / NC-value** — Frantzi, Ananiadou & Mima (2000)。自動術語辨識的經典,即 P2 的四象限。
- **Weirdness ratio** — Ahmad et al. (1999) → P7。
- **Termhood vs unithood** — Kageura & Umino (1996)。
- **GraphRAG** — Edge et al., arXiv 2404.16130。
- Reward hacking / Goodhart's law — 為什麼代理指標要靠下游真指標校準。
