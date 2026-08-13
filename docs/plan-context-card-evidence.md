# Plan — context card:證據與成品分離

## 三個現象,擁有者的原話

> 我們很常會看到他會自己發明定義(文本沒有說的他會自己說明)。我們真正會想要的是文本當中
> 明確定義的可以被記下來,並且和過去同樣名詞定義相融合。例如 a 說蘋果是水果、另一個文本 b
> 說蘋果是紅色,就應該最後呈現「蘋果是紅色的水果」。但現在充斥著蘋果的定義,包含他是什麼科
> 什麼屬(文本沒提過),以及每多張卡片在描述蘋果的不同面向。

> H2O2 是這次的材料 —— 在缺乏上下文的情況,這是無用的。

> title: 14k ratio,body: the 14k ratio increased from wave 1 7% to wave 2 20%。
> 這個訊息感覺是重要沒錯,但是我會預期 title 放 14k ratio 是在解釋什麼叫做 14k ratio。

歸成三種失敗,而其中兩種**不在 prompt 裡**:

| 現象 | 真正的位置 |
|---|---|
| 自己發明定義 | 抽取判準 —— 可以訓練 |
| 一個詞多張卡 | `merge_drafts` 只在 **norm_key 撞名**時合併 |
| 定義不融合 | **後到的 body 被靜默丟棄**(見下) |
| 答錯問題(講場合、講發現) | 抽取判準 —— 可以訓練,但引句閘門抓不到 |

### 「不融合」不是還沒做,是資料遺失

`kb/card_gen.py:390`:

```python
if not p.confident and d.confident:   # 只有「不確定 → 確定」才換 body
    p.title, p.body, p.confident = d.title, d.body, True
```

兩份文件都有把握地談蘋果時,後到的 body 直接被丟掉。**key 會 union、provenance 會 union、
body 從來不 union。** 跨 run 也一樣:`update` 走 `create_or_update` **整段覆寫**
(`card_gen_coordinator.py:266`)。同一類 bug 在這裡被咬過一次 —— 註解寫著 #518 補救
`reference_doc_ids`,「否則每一輪 card-gen 都會把卡上的證據刮掉」—— **body 有一模一樣的
毛病,沒被補。**

`Collection.auto_digest` 預設 `False`,所以出卡全部來自手動觸發。重跑同一批文件正是這兩個
缺陷疊加最兇的情境:不是覆寫掉上一輪,就是因為 key 差一點而長出兄弟卡。

## 根本的建模錯誤

「**每份文件各自寫一段定義,然後挑一個贏家**」。沒有任何一份文件握有完整圖像,所以無論挑誰
贏都一定丟資訊。這不是 merge 寫壞了,是這個模型**本來就不可能累積**。

## 正解

```
每份文件  →  關於某個詞的【陳述】,逐字帶引句      ← 只需要看這一份文件
                     ↓ 累積
一個詞的全部陳述  →  合成一段 body                 ← 這裡才需要全域視野
```

跟 #697 對圖譜做的是同一件事:mention 是逐段的證據,entity 由全部證據推導。

---

## 已定案

| 決定 | 理由 |
|---|---|
| **不與知識圖譜共用證據層** | 圖譜的 `attributes` 抽的是同一種東西,合併能省一趟呼叫。**但兩條路都還不夠好,擁有者正在賭哪一條比較好** —— 共用抽取等於共用失敗模式,比出來的差異只剩渲染方式。這是刻意的重複,**未來的讀者不要「順手」收斂掉**。 |
| **共用樣本與探針,不共用版本** | 尺可以共用,證據不行。`--from` 指文件,`--tune-round` 指版本。 |
| **不提問,改為沉默** | 原 prompt 只有「出卡 / 提問」兩條路(*When in doubt, ask; never guess*)。拿掉 ask,剩下的動詞只有 guess。第三條路是**什麼都不產出**。 |
| **拿掉 `confident: false` 那層** | 原定義是「grounded in the text but inferring」。inferring 就是發明的入口。 |
| **舊卡刪除,不寫相容** | 舊卡沒有 `statements`,無法重算。擁有者決定砍掉重生。 |
| **judge 必須先對人校準** | `defines_rate` 是唯一建立在模型判斷上的指標。未校準的 judge 會讓迴圈爬錯的山。 |

### 三種失敗,三個量法

| 指標 | 量什麼 | 需要校準嗎 |
|---|---|---|
| `grounded_rate` | 模型提出的陳述裡,引句真的在原文的比例 | 不用 —— 純字串比對 |
| `defines_rate` | 卡片有沒有回答「這個詞是什麼」 | **要** —— 唯一靠模型判斷的 |
| `lookup_hit_rate` | 讀者會查的東西,有沒有對應的卡 | 靠人看一次凍結探針 |
| `cards_from_one_document` | 只站在一份文件上的卡 —— 一詞多卡的殘留量 | 不用 |

`fitness = lookup_hit_rate × grounded_rate × defines_rate`,**相乘不是相減**:抽零張卡的
grounded 是空洞的滿分 1.0,相減會讓它奪冠。

## 兩個迴圈,順序不能顛倒

```
迴圈 A  校準 judge         人的時間,一次性
        20 張人工標註 → GLM 依分歧改寫判準 → 重新評分
        沒有 holdout ⇒ 跑三五輪就停
        用全新一批(--judge-from + --seed)驗證有沒有過擬合
                ↓
迴圈 B  訓練抽取 prompt     機器的時間,可無人看管
        每輪:抽卡 → 用【校準後的】判準評分 → GLM 改抽取 prompt
        有固定 holdout ⇒ 可以久跑
```

**A 的產出是 B 的評分依據。** `best_judge_prompt()` 挑一致率最高的**已評分**版本 —— 不是
最新的,因為校準是單線沒有 beam,退步的版本一樣會是最新的。

### 一個前提:judge 必須先對自己一致

實測:預設溫度下 judge 對同一批卡跑兩次只有 **16/20** 一致。一個跟自己都談不攏的評審不可能
穩定地跟人談得攏,而且**改 prompt 修不好那個** —— 那是取樣隨機性。

必須先:`reasoning_effort: "none"`,以及 `temperature=0`。

> ⚠️ **專案裡沒有 temperature 這個旋鈕** —— `litellm.completion` 沒傳它
> (`kb/llm.py`)。目前靠手改或在模型服務端設定。要做成設定的話,得先決定範圍:
> judge 要 0,檢索增強(multi-query / HyDE)大概不要。

---

## 進度

### 離線的部分 —— 完成

| | |
|---|---|
| `kb/cards/extract.py` | 一份文件 → 帶逐字引句的陳述;引不出原文(含空引句)就丟,並**計數** |
| `kb/cards/build.py` | 依 `lookup_glossary` 的正規化分組;body 由全部陳述推導;`--concurrency` |
| `kb/cards/preview.py` + `card_preview` CLI | 離線跑真實文件,零 store |
| `kb/cards/tune.py` | 迴圈 B:三層獎勵、beam、mini-batch、`DEFINES` 評審 |
| `kb/cards/calibrate.py` | 迴圈 A:`--review` / `--calibrate`,判準逐版留存 |
| `kb/tuning.py` | 兩條管線共用的版本記帳、beam、探針 |

### 接回正式管線 —— P1 完成,P2–P4 未做

| | | 狀態 |
|---|---|---|
| **P1** | `ContextCard.statements` + `accumulate()` + `synthesise()` 對外 | ✅ |
| **P2** | drafter 改吐陳述,換掉 `LlmCardDrafter.digest` | ⬜ |
| **P3** | commit 改成「附加 + 重算」,換掉 `merge_drafts` 的丟棄與 `update` 的覆寫 | ⬜ |
| **P4** | 待審 inbox 顯示陳述 + 引句,每句點得到出處 | ⬜ |

P3 是唯一動到現行審核流程的一步;舊卡刪除的決定讓它不用寫相容邏輯。

## 非目標

- 不與圖譜合併證據層(**刻意**的重複)
- 不做黑名單:被排除的名字不再被抽出,就不再出現在任何後續證據裡,沒有任何一輪能發現那個
  排除是錯的 —— 與 [`plan-graph-reward.md`](plan-graph-reward.md) 同一條理由
- 不做人工「必抓詞」名單:列得出那份名單,就代表已經知道答案了
- 不動模型權重

## 操作程序

見 [`plan-graph-reward.md`](plan-graph-reward.md) 的同型流程;卡片這條的指令:

```bash
# 0 一次性:抽樣本、產一批卡
python -m workspace_app.graph_preview <cid> -o ./rounds --dump-samples 60 --holdout 30
python -m workspace_app.card_preview --samples ./rounds/holdout -o ./card-check --concurrency 8

# A 校準 judge(重複到一致率不動,不要 while 迴圈)
python -m workspace_app.card_preview --review ./rounds-cards --cards ./card-check/cards.json
#   看完 20 張,只標不同意的
python -m workspace_app.card_preview --calibrate ./rounds-cards
#   用全新一批驗證
python -m workspace_app.card_preview --review ./check-1 --cards ./card-check/cards.json \
    --judge-from ./rounds-cards --seed 1

# B 訓練抽取 prompt(可掛著跑)
while :; do python -m workspace_app.card_preview --tune-round ./rounds-cards \
    --from ./rounds --batch 8 --holdout-every 3 --concurrency 8 || break; done
```

**驗證用的批次絕對不要拿去 calibrate** —— 一旦拿去改判準,它就不再是 holdout。
