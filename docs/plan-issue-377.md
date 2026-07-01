# Plan — #377 AI 針對 doc 主動詢問不懂的地方

> 狀態：grill-me 鎖定，待 /tdd。flat integer phases（P1、P2…）。

## 問題與目標

AI 讀 doc、整理知識時，遇到不認識的名詞/專用詞/簡寫，常會**腦補一個看似合理的定義**寫進知識庫（context card / wiki），人再順手核准 → 錯誤知識入庫。

**#377 的核心精神**：讓「寫知識的那個 AI」**變聰明——不懂就問、不硬寫**；問題另存成獨立紀錄，由人在專屬頁面回答；答案回灌 → 詞類定義變 context card、描述/邏輯變 wiki。杜絕「不認識 → 腦補 → 寫進 KB」。

## 一句話流程

`card_gen 逐份細讀 doc`（同一次 LLM pass）分三種產物：

1. **有把握定義的詞** → 起草**卡片提案**，走現有 Context Cards 分頁人審（維持不變）。
2. **不懂的詞** → 開一題 **詞類 `DocQuestion`**（collection 層以 `norm_key` 去重，記錄哪些 doc 提到它）。
3. **讀不懂的段落** → 開一題 **描述 `DocQuestion`**（帶 `{source_doc_id, quote, question_text}`）。

人在**全域收件匣**回答：詞類答案 → AI 整理成「標題＋定義」建/更新 context card；描述答案 → 寫進該 collection 專屬的**「澄清」wiki 頁**（逐條忠實附加、免疫 wiki builder 覆蓋）。

## 鎖定決策（grill-me Q1–Q13）

| # | 主題 | 定案 |
|---|------|------|
| Q1 | 本質機制 | **諮詢式**、非 gating；問題另存、由人在**另一頁**回答；ingestion 不受影響 |
| Q2 | 題目顆粒度 | 新 `DocQuestion` resource；**詞類題 collection 層以 norm_key 去重**（記錄來源 doc 清單）；描述題綁單一 doc |
| Q3 | 與生成器關係 | **同一個 AI 變聰明**：card 起草時無法從原文有把握定義 → 不起草、改開一題；人答後才寫 |
| Q4 | 題目來源 | 一律從 **card_gen 逐份細讀** 冒出（單一 digest 同時吐詞類＋描述題）；**wiki builder 不改**，只當描述類答案的目的地 |
| Q5 | 觸發時機 | **每個 collection 一個開關，預設手動**；開啟後在 doc `ready` 後自動 digest（hook 仿 wiki/quality）；手動也可重跑 |
| Q6 | 判斷「不懂」 | **LLM 自判信心 ＋ 三道硬護欄**：①已有 card 的詞跳過 ②文件/語料自己有夠用解釋就不問（能定義就寫卡）③每份 doc 上限 N（可設定，預設 5） |
| Q7 | 分類歸屬 | **AI 分類固定**（詞類→card／描述→wiki，即決定落地目的地）；分錯的補救＝人**丟棄**該題（不重新分類） |
| Q8 | 回答頁面 | **全域跨-collection 收件匣**（頂層頁）；只列使用者**能編輯**的 collection 的題（無完整 ACL 前＝等同現狀可見） |
| Q9 | 答案落地 | **直接生效、信任人的答案**（不再走一次 AI 詮釋）：詞類→建/更新 card；描述→寫澄清 wiki 頁 |
| Q10 | wiki 落地點 | 每 collection 一頁**「澄清」wiki 頁**，逐條 Q&A 忠實附加、不動既有頁、不經 AI 重寫；**免疫於 wiki builder 覆蓋** |
| Q11 | 重跑語意 | 已有 open 題→只併來源 doc、不重開；已答/已有卡→跳過（護欄）；**被丟棄的詞之後又出現→重新開題**（丟棄非永久） |
| Q12 | 描述題顆粒度 | **引用看不懂的原文片段 ＋ 一個聚焦問題**；題目帶 `{source_doc_id, quote}`；澄清頁忠實呈現「片段＋人的答案」可溯源 |
| Q13 | 三項確認 | ①有把握的詞**仍照現有起卡提案走人審** ②收件匣**只看可編輯 collection** ③詞類答案→**AI 整理成標題＋定義**（keys 用題目那個詞） |

補充（用預設值收斂、已獲確認）：
- **上限 N 預設 5**、可在 collection 層調。
- **描述題「丟棄後再問」**：限定「**出現在新的來源 doc** 才重開」，同份 doc 重跑不會一直冒。

## 資料模型

### `DocQuestion`（新 specstar Struct + Model）

```
class DocQuestion(Struct):
    collection_id: Annotated[str, Ref("collection", on_delete=cascade)]
    kind: str            # "term" | "description"
    status: str          # "open" | "answered" | "discarded"
    question_text: str   # AI 生成的問題

    # 詞類專用
    term: str = ""             # 作者面表層詞（如 "M4"）
    norm_key: str = ""         # derived（norm(term)），去重/查詢用
    source_doc_ids: list[str]  # 提到此詞的 doc 們（去重併入）

    # 描述專用
    source_doc_id: str = ""    # 引發此題的單一 doc
    quote: str = ""            # 看不懂的原文片段

    # 回答/落地
    answer: str = ""           # 人的答案（answered 後）
    result_ref: str = ""       # 生成的 card id 或澄清頁 path（溯源）
```

- `indexed_fields = ["status", "collection_id", "kind", "norm_key"]`（去重查詢＋收件匣列表＋page aggregate 都要 indexed；遵守 specstar indexed-queries 紀律，不 fetch-all + Python filter）。
- specstar metadata（created/updated time、updated_by、revision）自動帶，勿重複定義。

### `Collection` 新欄位（開關）

- 一個 per-collection 布林/enum 欄位（如 `digest_questions_enabled: bool = False`，或沿用類似 `quality_rubric` 的「有設才跑」樣式）→ 預設關（手動）。
- （可選）`max_doc_questions: int | None` per-collection 覆蓋預設 N。

### 澄清 wiki 頁

- 每 collection 一個保留 path（如 `_clarifications` / `澄清.md`），存於 `WikiFileStore`。
- **必須免疫於 wiki builder 覆蓋**：builder 的頁面管理要略過此保留 path（或以「human-authored」旗標保護），如同人答的 card 靠「已有卡跳過」護欄保住。

## Flat-phase 計畫

> 每個 phase 完成即 commit 一次（一 phase 一 commit，本地不 push）。TDD red-green-refactor。後端 100% coverage gate；FE 走 vitest（FE TDD 紀律）。

- **P1 — `DocQuestion` resource + 查詢**
  - Struct/Model、`indexed_fields`、norm_key derive、collection cascade。
  - 去重輔助（`open_question_for(collection, norm_key)`、併入 source_doc / 建新題）、狀態轉移（open→answered/discarded）。
  - 收件匣查詢（scoped by collection ids、status=open），走 `exp_aggregate_by`/QB，不 fetch-all。

- **P2 — 擴充 `CardDrafter` / card_gen digest**
  - 單一 LLM pass 產出 `{confident_cards, term_questions, description_questions}`（streaming，累積 chunks）。
  - 三道護欄：跳過已有 card 的詞（沿用 `classify_against_existing`/`cards_for_collections`）、文件自己有解釋就不問、每份 cap N。
  - confident_cards 照舊落 job `artifact` 人審；questions 寫入 `DocQuestion`（詞類 collection 層去重併來源、描述帶 quote）。

- **P3 — 觸發：collection 開關 ＋ 自動 hook**
  - Collection 開關欄位（預設關）。
  - `IndexCoordinator` 於 doc `ready` 後，若該 collection 開關開 → 自動 enqueue digest（仿 `_quality_hook`／wiki hook；失敗安全、以 doc owner 身分）。
  - 手動路徑維持（既有 card_gen enqueue，順帶產題）。

- **P4 — 回答落地**
  - 詞類 answer → AI 把答案整理成「標題＋定義」→ 建/更新 context card（沿用 `author/edit` action、derive norm_keys）；`result_ref` 記卡 id；題目 status→answered。
  - 描述 answer → 附加到澄清 wiki 頁（忠實：quote＋answer 一節）；builder 免疫；`result_ref` 記頁 path；status→answered。
  - 丟棄 → status→discarded。

- **P5 — FE 全域收件匣 + 澄清頁呈現**
  - 頂層「待釐清」inbox 頁（TanStack Query）：列 open 題（詞類一詞一列＋來源 doc、描述帶 quote），答／丟棄；權限＝可編輯 collection。
  - Collection 開關的 FE 入口（Context Cards 或 collection 設定處）。
  - 澄清 wiki 頁在既有 WikiBrowser 正常呈現。
  - UI copy 不露內部名詞（i18n 台灣用語）。

- **P6 — 重跑語意 ＋ 邊界**
  - 去重／丟棄再問（描述題限「新來源 doc」才重開）。
  - 邊界：doc/collection 刪除的 cascade、cap N、collection 無開關時 no-op、空語料、失敗安全。
  - 收尾：`ruff` / `ty`（whole-project）/ 100% coverage gate；FE `typecheck` + build。

## 沿用既有機制（不重造輪子）

- `card_drafter_llm`（現有 LLM）、`CardDrafter`/`CardGenCoordinator` 機制。
- context card：`author/edit` custom action、`norm()`/`derive_norm_keys()`、`classify_against_existing`、`cards_for_collections`。
- `WikiFileStore`（每頁一個 `WikiPage`，id=`{collection_id}/{path}`）。
- coordinator/JobType 樣式（`build_coordinators`、`partition_key`、`preserve_job_creator` 以 owner 身分寫回）。
- specstar：indexed queries + `exp_aggregate_by`（page 聚合 scoped）、migration/backfill（若對既有 collection 需要）。

## 非目標（Out of scope）

- gating（擋住產出直到人回答）——本案是諮詢式。
- AI 把描述答案整合進既有 wiki 頁 / 丟給 wiki builder 重寫——一律走忠實的澄清頁。
- 完整 per-collection ACL——沿用現狀可見性。
