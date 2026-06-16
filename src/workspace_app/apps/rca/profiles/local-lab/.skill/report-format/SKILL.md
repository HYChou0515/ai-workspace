---
name: report-format
description: 寫 ./report.v{N}.md 的版型細則 — 要數字 + 圖,findings 嚴格按 a→b→c→d 四段順序、按 physical priority 排。當你進入 SOP step 6、或使用者要求 draft / 修報告時使用。
---

# Local-lab 報告版型(細則 + 反模式)

系統 prompt 已經規定整份報告的骨架:**Problem statement** → **Findings (1..K)** → **Next steps**,findings 嚴格 a→b→c→d。這份 skill 把細節、好/壞示範、跟反模式列出,幫你寫出讓使用者(in-house RCA 工程師)讀得進去的版本。

## 整份報告 = 三大塊

```
1. Problem statement (1 段)
2. Findings (1..K 段) — 按 physical priority 排,每段嚴格 a→b→c→d
3. Next steps (1 段)
```

## 1. Problem statement(1 段)

把 `./brief.md` 的核心數字濃縮成一段:

- wafer 範圍 / 數量(`X 片 defective / Y 片 baseline`)
- defect type
- threshold(數值 + hard 或 soft)
- scan stage(unknown 就寫 unknown,不要編)

**不寫**業務 context、團隊、impact 估算、客戶 PO — 不是這份報告的事。

## 2. Findings — 按 *physical priority* 排,不是按 score

**高分 ≠ 物理有道理。** Score 是純統計訊號;排序時要結合 KB 過濾 (step 4) + domain 判斷。例:

- 某 step 在 `rank-factors-categorical.csv` 排第 1,但 KB 從沒對應 case + 物理機制跟此 defect type 不合理 → **排後或剔除**
- 某 step 在排序第 5 但 KB 有 3 個歷史 case + 製程上有明確 mechanism → **可能排第 1**

跟使用者一起決定最終順序。報告應該寫出 **2–4 個物理上撐得起來的候選**,不是只挑最高分那 1 個 — 讀者要能看到「為什麼這幾個是真候選、剩下的為什麼被排除」。

### 每個 finding 嚴格照 4 段順序(a → b → c → d)

**順序不能跳、不能倒。** 結論先,假說次之,數據圖表三,KB 引用四。

#### a. Conclusion(1 句)

> *what / where / by how much*

**禁止虛詞**。寫具體數字 + tool / step / measurement 名稱。

✅ 好的例子:
- 「step 380 `CMP02.CHB-3` 在 defective wafer 上的命中率是 baseline 的 6.4×(11/12 vs 6/38)。」
- 「`Gate_CD` 在 defective wafer 上 median 漂出 USL +1.8σ;`n_fail=11`、`perm_p=0.002`。」

❌ 不合格:
- 「step 380 看起來有問題。」
- 「可能跟某個 CMP tool 相關。」
- 「rank-factors 排第一名是 X。」(這是過程,不是結論)

#### b. Hypothesis(1–2 句)

> *why* 這 step / tool / chamber / recipe / measurement 會讓 **defect type X** 變多。

用製程物理 / 設備機制描述,**不是統計重複**。

✅ 好的例子:
- 「CMP 過拋讓 Cu dishing 增大,後續 M1 metal layer 的 pp seam 在 defect inspection 上被讀成 merge defect。」
- 「`CHB-3` 的 head pressure 飄高,導致此 step 上的 wafer 局部過拋,雖然 averaged WIP measurement 還在 spec 內。」

❌ 不合格:
- 「因為 Fisher's exact `score` 很高,所以這 step 是 root cause。」(統計重複,不是物理假說)
- 「可能是設備問題。」(沒有 mechanism)

#### c. 數據 + 圖表(必含具體 row + 至少一張圖)

##### c.1 數據 — 直接引 ranking CSV 的具體 row

對 categorical:

| step | factor | factor_value | score | a | b | c | d | p_value |
|------|--------|--------------|-------|---|---|---|---|---------|
| 380  | tool+chamber | CMP02.CHB-3 | 18.7 | 11 | 1 | 6 | 32 | 1.3e-8 |

對 numerical:

| measurement | score | separation | tau   | n_fail | perm_p |
|-------------|-------|------------|-------|--------|--------|
| Gate_CD     | 0.91  | 0.95       | +0.71 | 11     | 0.002  |

把 `a / b / c / d`(contingency)或 `n_fail / perm_p / sep_directional` 這類診斷欄一起貼,讓讀者看到「分數背後的支撐強度」。

##### c.2 圖表 — 嵌入 plot 系列產的 PNG

每個 finding 至少嵌一張視覺化圖。**唯一支援的語法**是 markdown 圖片引用:

```markdown
![CMP02.CHB-3 vs others, step 380](./binary-scatter-step380.png)
```

FE 的 report renderer 會把 `./xxx.png` 自動解析成 workspace 內的檔案 URL,所以圖會直接 inline 顯示。

**禁用以下做法**(這些 FE 不會渲染,使用者只看到一堆亂碼/空白):

- ❌ `<img src="data:image/png;base64,iVBOR..."/>` — base64 內嵌,renderer 用 react-markdown 預設不開 raw HTML,圖不會出現
- ❌ 把 PNG 內容用文字塞進報告
- ❌ 只給 link (`[plot](./plot.png)`),不給 image (`!`)

**檔案放在 workspace 根目錄**(跟 report.v{N}.md 同層),路徑用 `./filename.png` 或直接 `filename.png` 都可以。

圖檔通常是 `binary-scatter` / `categorical-scatter` / `categorical-ordinal-series` / `ordinal-series` / `time-series` 跑出來的,或自己 `exec` 寫 matplotlib script 產的(sandbox 內 `python` / `python3` 都接到 python-stack,直接 `import pandas, numpy, scipy, matplotlib`)。

**每張圖下面加 1–2 句解讀**(圖上看到什麼,而不是只貼圖就走)。

**不嵌圖 / 沒具體數字 / 用 base64 內嵌 = 這 finding 段不合格,要重寫。**

#### d. KB references

列 step 4 用 `ask_knowledge_base` 拿到的 citation。**只要在 markdown 裡寫 `[N]` + filename + 一句 hypothesis-relevant 摘要**;FE 的 report renderer 會自動在報告底部生一個 **Sources panel**(從這個 investigation 的對話 history 裡 `ask_knowledge_base` 工具的 citation 自動撈出來,**點 card 會在 KB 文件檢視器打開對應段落並 highlight**)。

格式:

```
- `[12]` 06-source-drain-epi.md — PP merge 定義為相同 OD 內相鄰 fin 的 epi 層合併
- `[15]` 05-defects-material.md — PP merge 的主要製程關聯偏向 epi 生長控制
```

`[N]` 的數字必須**跟 `ask_knowledge_base` 回的 citation marker 對齊**(tool message 的 `citations` 陣列每筆有 `marker` 欄)。對齊了,Sources panel 才能跟正文的 `[N]` 對得起來。

**沒拿到相關 citation → 明寫**「KB 無歷史 case 對應此 hypothesis」。**禁止編**沒有的 case ticket、URL、quote — 那是嚴重 hallucination。Sources panel 也只能從真實 tool 回傳出來,你編的不會被加進去。

## 3. Next steps(1 段)

- **下一輪要收什麼資料** — 例:特定 wafer 的 SEM、CMP02 的保養 log、recipe diff 對照
- **可能的對策方向** — **只開頭一句**,不要寫成 D5–D7 那種對策表(那是另一個流程)
- **假說的最大不確定** — 還缺什麼資料才能 confirm / reject

## 反模式(看到就要重寫)

| 反模式 | 為什麼錯 | 怎麼修 |
|--------|---------|--------|
| 在 finding section 寫「資料 → 假說 → 結論」(倒序) | 讀者要先知道結論,才有耐心看後面 | 結論先 (a),回到 b → c → d 順序 |
| 一個 finding 只有文字、沒數字、沒圖 | 撐不起來、讀者不信 | 補 c 段:具體 row + 至少一張圖 |
| 整篇報告只有最高分的 1 個 finding | 失去物理排序的價值;讀者看不到排除過程 | 列 2–4 個候選,被排除的也寫一兩句說為什麼 |
| 引用 KB 時改寫 title / 編 URL | citation 必須跟 `ask_knowledge_base` 結果嚴格 1:1 | 直接 paste 回的 metadata;沒有就寫「KB 無對應」 |
| 把所有結論塞同一個 root-cause 段(不分 finding) | 失去 a→b→c→d 結構,讀者要自己拆 | 每個 finding 獨立一個 section,內部嚴格 4 段 |
| 用 base64 / `<img src="data:...">` 內嵌圖 | FE renderer 不開 raw HTML,使用者看到一堆亂碼或空白 | 改用 `![alt](./filename.png)`,圖檔放 workspace 根目錄 |
| 圖檔生出來但沒嵌進報告 markdown,只在對話裡口頭描述 | 報告本身缺資料,export PDF 出來是空的 | 在 c.2 段用 `![](./filename.png)` 把每一張圖正式嵌入 |

## 範例骨架(不是模板,別逐字 copy)

```markdown
# Investigation Report — `<investigation title>` (v1)

## Problem statement

12 片 defective wafer(`defect_count ≥ 6`)/ 38 片 baseline;
defect type `pp merge`;threshold `6`,soft。
Scan stage:unknown(本輪 analysis all)。
資料來源:`./defects.csv`、`./wafer-history.csv`、`./measurements.csv`。

## Findings

### Finding 1 — CMP02.CHB-3 過拋 / Cu dishing

**a. Conclusion.** step 380 `CMP02.CHB-3` …(具體數字)。

**b. Hypothesis.** CMP 過拋 → Cu dishing → M1 pp seam …(物理機制)。

**c. 數據 + 圖表.**

| step | factor | factor_value | score | a | b | c | d | p_value |
|------|--------|--------------|-------|---|---|---|---|---------|
| 380  | tool+chamber | CMP02.CHB-3 | 18.7 | 11 | 1 | 6 | 32 | 1.3e-8 |

![binary scatter step 380](./binary-scatter-step380.png)

圖上看到 …(1–2 句解讀)。

**d. KB references.**

- `[12]` Case TKT-2024-0817: M1 pp seam at CMP-suspect lots
- `[15]` Process spec CMP02-rev3.pdf §4.2

### Finding 2 — Gate_CD drift(次要候選)

…(同樣 4 段)…

### Finding 3 — Tool E12 排在第 1 但被排除

雖然 `rank-factors-categorical` 把 step 420 `E12` 排在第 1(score 21.3),
但 KB 沒有歷史 case 對應、且 E12 是純機械搬運不涉製程化學,**從候選剔除**。

## Next steps

下一輪要 …(資料 + 對策方向 + 不確定)。
```
