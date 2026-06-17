# Topic Hub

**Topic Hub** 是一個「跨 collection、會隨時間累積知識」的探究工作區。你把資料丟進來、針對它聊天（單獨或多人）、再用 workflow 把內容萃取成可長期保存的**記憶（memory）檔案**，並把文件歸檔到正確的知識庫 **collection** —— 全部集中在同一個你會反覆回來的 item 裡。

> 這是使用導向的說明。完整的設計規格與決策理由請見 `docs/topic-hub.md`。

---

## 心智模型

- **一個 Hub = 一個 item。** 每個 Hub 都是一個含**檔案工作區 + agent + 多個聊天 + collection 清單 + 記憶檔案**的工作區。
- **能長期保存的東西都是檔案。** 記憶（`MEMORY.md` + `memory/`）、collection 清單（`collections.json`）、workflow 產物 —— 都是工作區裡的檔案，可由 agent 或人（在 IDE 裡）編輯。
- **聊天即 workflow。** 每個聊天分頁不是 free chat（人主導），就是 workflow chat（由 workflow 的流程驅動）。
- **記憶永遠在 agent 眼前。** 每一輪對話都會自動把 `MEMORY.md` 與 `collections.json` 的**當下內容**注入給 agent（不寫進歷史、每輪重新讀取），所以 agent 永遠看到最新狀態。

---

## 快速開始

1. 在 launcher 建立一個 **New Topic Hub**，取個能代表主題的標題。
2. 建立時會自動 seed 三個檔案：`MEMORY.md`、`memory/`（放較深入的筆記）、`collections.json`（初始為 `[]`）。
3. 開一個聊天就能開始問問題；或上傳資料後跑 workflow 把它消化進記憶 / 歸檔進 collection。

---

## 聊天

- 右側面板上方是**分頁列**：可同時開多個聊天（free chat 與 workflow chat 並存）。
- **+ New chat** 開一個 free chat（人主導的自由對話）。
- **Run workflow ▾** 開一個 workflow chat（由下方某個 workflow 的流程驅動，見下節）。
- 聊天本體就是完整的 agent 面板：模型/思考強度選擇器、suggestion chips、`@mention`、附檔、undo、`⌘↵` 送出、reasoning 折疊、工具卡片、引用。

agent 回答問題時，**由便宜到昂貴**依序取用三層來源：

1. **記憶** —— 直接用每輪注入的 `MEMORY.md` 與 `memory/*.md`。
2. **詞彙表（glossary / context cards）** —— 遇到不認識的術語、縮寫時呼叫 `lookup_glossary`，從 Hub 的 collection 對應的 context card 取得權威解釋（不做搜尋、即時）。
3. **知識庫文件** —— 前兩層都不夠時才呼叫 `ask_knowledge_base`，對 Hub 的 collection 文件做檢索（最慢，最後手段）。

---

## 管理 collection 清單

Hub 的 collection 集合是工作區檔案 `collections.json`（一個 `[{ "id": ..., "name": ... }]` 的清單），它同時是：

- 聊天時的**檢索範圍**（`lookup_glossary` / `ask_knowledge_base` 只看這些 collection）；
- `→collections` workflow 歸檔文件時的**候選 collection**。

有兩種改法：

- **用聊天**（推薦）：直接跟 agent 說「把 *equipment-log* 這個 collection 加進來」。agent 會用 `resolve_collection` 把你給的 id 或名稱解析成正規的 `{id, name}`，再用檔案工具寫進 `collections.json`。
  - 給的名稱對不到時，`resolve_collection` 會回傳可用的 collection 清單；名稱有多個相符時會列出候選讓你指定。
- **直接編輯**：在左側檔案 IDE 打開 `collections.json` 手動增刪，存檔即生效。

---

## 記憶模型

- **記憶就是檔案**：`MEMORY.md` 是永遠在 context 裡的**精簡索引**；較深入的內容放在 `memory/*.md`，agent 需要細節時才讀。
- **結構是慣例、不是 schema**：要分「事實 / 假設 / 決策」之類，就用檔案組織與行內標註（例如 `memory/decisions.md`、`(未確認)` 標記）表達，由 workflow 的輸出格式決定，沒有硬性欄位。
- 記憶由 workflow 建立與維護（見下節），也可由 agent（檔案工具）與人（IDE）自由編輯。

---

## Workflows

從聊天分頁列的 **Run workflow ▾** 啟動。每個 workflow 是 produce → review → commit 的流程，產物落在工作區檔案裡，可重跑（已完成的步驟會跳過，只重跑改動到的部分）。

預設 profile 內建三個：

### 1. `→memory` — 把上傳資料消化進記憶（batch）

把丟進來的檔案逐一萃取成記憶筆記，再刷新 `MEMORY.md` 索引。

**用法**

1. 在檔案 IDE 的 `inputs/` 資料夾放入要消化的檔案。
2. **Run workflow → Digest uploads into memory**。
3. 流程：
   - **digest**：每個 `inputs/*` 檔案 → 一篇 `memory/<檔名>.md` 筆記（關鍵事實 / 決策 / 待解問題）。
   - **index**：依這些筆記重寫 `MEMORY.md`，使它成為當下的精簡索引。
4. 無人工關卡。想只重跑某一篇，改那篇筆記後再 Run 即可（只會跑改動到的步驟）。

> 進階：可放一個 `inputs/input.json` 用 `{"files": [...], "except": [...]}` 自訂要納入 / 排除的檔案範圍（`inputs/input.json` 本身預設被排除）。

### 2. `→collections` — 把上傳文件歸檔進 collection（batch）

逐一分類每個檔案、寫摘要、收集生詞，**你先核可路由與詞彙表**，再實際歸檔並產生 context card。這是「produce → review → commit」的典型流程，而**審查的內容放在檔案裡**。

**前置**：`collections.json` 至少要有一個 collection（否則回傳 `no_collections`）。

**用法**

1. 在 `inputs/` 放入要歸檔的文件。
2. **Run workflow → File uploads into collections**。
3. 流程：
   - **classify**：每個檔案挑一個 Hub 既有的 collection、寫一行摘要、列出新手不懂的術語 → `plan/<檔名>.json`。
   - **glossary**：把所有收集到的術語寫成填空檔 `glossary.todo.md`（每個術語一個 `## <術語>` 區塊，下面留空）。
   - **review（人工關卡）**：在 IDE 打開 `glossary.todo.md`，**在每個術語下面填上定義**（也可以另開一個聊天請 LLM 幫你填，所有聊天共用同一份檔案）。填好後回到該 workflow chat 按 **Continue**（或 Reject 取消）。
   - **commit**：把每個檔案 `ingest_to_collection` 歸檔，並為每個**有填內容**的詞彙產生一張 context card。
4. 在核可前不會有任何東西進到 collection；核可後重跑會跳過已完成的步驟。

> 留空沒填的詞彙區塊不會產生 card，只有非空白的會。

### 3. `→consolidate` — 整理記憶（single）

重讀目前的記憶（與近期聊天），重寫記憶檔：合併重複、精簡冗長、**刪掉過時或被取代的內容**。

**用法**

1. **Run workflow → Consolidate memory**（不需上傳檔案）。
2. 單一節點讀 `MEMORY.md` 與 `memory/*.md`，重寫 `MEMORY.md` 為更精簡的當下索引，並整理筆記（必要時刪除整篇過時筆記）。
3. 採 last-write-wins；由人或外部排程器手動觸發 —— 平台本身沒有內建排程器。

> 進階：呼叫端可在 inputs 提供 `{"context": "<近期聊天摘錄>"}`，把近期對話一起納入整理（workflow 函式庫本身不直接讀聊天）。

---

## 檔案結構速查

| 檔案 / 目錄 | 用途 |
| --- | --- |
| `MEMORY.md` | 永遠注入 context 的精簡記憶索引 |
| `memory/*.md` | 較深入的記憶筆記（需要時才讀） |
| `collections.json` | Hub 的 collection 清單 `[{id, name}]`（檢索範圍 + 歸檔候選） |
| `inputs/` | 丟給 `→memory` / `→collections` 消化的上傳檔案 |
| `inputs/input.json` | （選用）自訂 workflow 的檔案範圍 / context |
| `glossary.todo.md` | `→collections` 產生的詞彙填空檔（人工填定義） |
| `plan/<檔名>.json` | `→collections` 每個檔案的分類結果（中間產物） |

---

## 多人協作與可見性

- **群組聊天沿用平台既有能力**：任何登入使用者都能在 Hub 的聊天裡發言，訊息會標記作者、即時廣播給在看的人，`@mention` + 通知都可用。
- v1 可見性採平台預設（所有內部登入使用者皆可存取）；尚無 per-item ACL 或私有 / 團隊 / 組織層級（待 SSO/authz 後再做）。
