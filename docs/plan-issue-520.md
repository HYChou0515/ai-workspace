# #518 + #520 — 卡片連文件、檢索收斂，與「圖片 → 知識」起手範本

一個 PR 交付兩張 issue：**#518**（context card 可以掛參考文件、檢索能被收斂到那些文件、
workflow DSL 能把剛建好的資源 id 傳給下一步）與 **#520**（出貨一個把上傳圖片變成
「可搜尋文件 + 連結卡片」的起手範本）。

#520 的 issue 內文寫「no engine code (all primitives already exist)」——那是**假設 #518 已經
landed**。實際上 master 裡連 `reference_doc_ids` 這個字都不存在，`_CAP_OUTPUTS` 也只有
`create_entity` / `send_notification`，所以四塊 engine 都得動。

## 定案（grill 後）

| # | 決定 | 依據 |
|---|---|---|
| 1 | 完整做 #518 再做 #520，一個 PR | user |
| 2 | ingest/upsert 的 journal 收據「有取 `name` 才改形狀」 | user；不弄壞現有 `file-uploads` |
| 3 | workflows 補兩件：共用登錄表 + 複製流程 | user |
| 4 | 複製入口只做 API + FE 按鈕，不做 agent tool | user |
| 5 | 撞名回 409，FE 問過才覆蓋 | user；與 file / document 路由一致 |
| 6 | 範本列表全部列，不相容標原因 | user |
| 7 | 卡片命中＝寬鬆比對 + 多張取聯集 | `keys` 是術語表面形式、key↔card 多對多 |
| 8 | 正向過濾走 `source_file_id`，OR `source_doc_id` 當舊資料 fallback | #104 註解明寫這是它的用途 |
| 9 | 空指標容忍，過濾後空集合退回全域搜尋 | 照 `move_document` 既有慣例 |
| 10 | 不需要 app 級 opt-in 名單 | 既有工具上限已經驗得精準 |

## Phases

| Phase | 內容 |
|---|---|
| P1 | `ContextCard.reference_doc_ids` 欄位（optional / additive） |
| P2 | 六個全量覆寫寫入路徑全部帶著它；export/import 改帶路徑 |
| P3 | Retriever `restrict_to_doc_ids` |
| P4 | `kb_search` card-anchored 兩段式 + 退回 |
| P5 | DSL capability 輸出鏈結 `{steps.ingest.doc_id}` |
| P6 | `sample-workflows/` + `SHARED_WORKFLOWS` + `image-to-knowledge` 範本 |
| P7 | 範本列出 / 複製 API（409 + overwrite + 相容性） |
| P8 | FE 範本區 |
| P9 | 文件（本檔 + `workflows.md` §22.9 / §22.10） |

## 三個實作上真的會咬人的地方

### 1. 新欄位會被「全量覆寫」的寫入路徑靜默清空

`ContextCard` 的每一條寫入路徑都是**重建整個 struct**，不是 patch。所以一個它們都不認識的
新欄位，會在下一次有人碰那張卡片時被清掉——欄位看起來能用，然後在正式環境自己慢慢變空。

六處都得串：capability 的 create / update / upsert、card-gen commit、`land_term_answer`、
以及 export / import。`update_context_card` 與 HTTP patch body 因此把
`reference_doc_ids` 做成**三態**：不給＝保留、給 list＝取代、給 `[]`＝清空。只想更新定義的
呼叫端（card-gen 一輪、回答一個問題、修個錯字）不該讓卡片賠掉別人策展的證據。

export / import 帶的是**路徑不是 id**：doc id 內含 collection id，直接搬 id 進另一個
collection 保證每條連結一落地就是空指標；路徑本來就是成員檔案的通用貨幣，import 時對目標
collection 重新編碼即可。

### 2. 正向過濾不能只看 `source_doc_id`

直覺做法是 `source_doc_id IN (...)`，鏡像既有的 `exclude_doc_ids`。**這是錯的。**
#104 之後 chunk 是綁**內容**的：同樣的位元組上傳到兩個路徑只有**一組** chunk，而那組
chunk 的 `source_doc_id` 只記其中一個擁有者。

實測（同樣位元組存成 `first.md` 與 `copy.md`）：共 2 個 chunk，`source_doc_id` 只有
`first.md`，`copy.md` 完全沒出現。所以卡片若掛的是 `copy.md`，正向過濾會**一筆都撈不到**，
而卡片看起來設定得好好的。`source_doc_id` 另外還在退休中（`""` 預設就是它的 retirement
surface），正向過濾在它上面會從「能用」直接衰退成「靜默比對不到」。

改成**先比對內容雜湊 `source_file_id`，再 OR 上 `source_doc_id` 給 pre-#104 舊資料**，
衰退方向就變成「精準度略降」而不是「整個失效」。這也正是 #328 overlay 已經在用的配對。

### 3. 策展一張卡片，絕不能讓搜尋變得更差

連結天生會失效：文件被刪、被改名（會鑄出新 id）、指到這次沒搜的 collection、或指到讀者無權
看的文件。任何一種都可能讓收斂後的候選集變成空的。如果空集合就回「查無結果」，那麼**策展
卡片就成了讓搜尋變爛的方法**——這比不策展還糟。

所以：無權限的 id 在組範圍前就先剔除，而收斂後的搜尋只要沒東西就**放寬成一般全域搜尋**。
一條退路涵蓋所有「範圍為空」的成因。策展一張卡片最壞只是多跑一次查詢，絕不會少給使用者
本來就該拿到的答案。

## 順手修掉的既有問題

- **5 個 dense arm 有 4 個沒套權限排除**（`exclude_doc_ids` 只串了主 arm）。在有接 code /
  image embedder 的部署上，被擋的文件可以從那些 arm 溜進 RRF 池。改成一個綁定好 scope 的
  `dense()` 探針，從根本消掉這類 bug，而不是多加第四個會忘記的地方。
- **`_CAP_OUTPUTS` 身兼兩職**——既是「有哪些可引用欄位」也是「必須有 `name`」。把 ingest 加
  進去會讓所有匿名 ingest step 驗證失敗，包含出貨的 `file-uploads`。拆成獨立的
  `_CAP_NEEDS_NAME`。

## 另開 issue（不塞進本 PR）

`exclude_doc_ids` 只比對 `source_doc_id`。在 #104 內容共用的情況下，這條排除同樣有不對稱：
排掉某份文件可能連帶讓共用內容的另一份合法文件也搜不到，或反過來沒排乾淨。屬於既有行為，
與本 PR 的正向路徑獨立。
