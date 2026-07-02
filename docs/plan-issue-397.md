# Plan: 讓 AI 修改 wiki 的工具 + 即時指正 UX (#397)

> **核心概念**:不讓 AI(或使用者)直接動 wiki 檔,而是收一份「**修正指示**」——
> 哪裡錯 / 該怎樣,外加可選的**範本文獻**——交給既有的 wiki maintainer agent 去改。
> 類比 #377:Q&A 塞的是「問答」,這個工具塞的是「修正 prompt」(由 AI + 使用者
> 共同產生)。修正的**事實**會存進 builder 免疫的保留頁,防止之後 fold 新文件時
> 把錯的寫回來。

本計畫由 `/grill-me` 逐題鎖定(Q1–Q16),見 §1。實作走 `/tdd`,flat phase(見 §5)。

> **進度**:P1–P5 全部 TDD 完成、每 phase 一 commit。後端單元測試綠、ruff/ty
> 全專案清、FE vitest + tsc + build 綠。P6(DoD gate + PR)進行中。

---

## 1 · 鎖定的決策(grilling Q1–Q16)

| # | 決策 |
|---|---|
| **Q1** | **混合生命週期**:提交即排 job 讓 maintainer 立刻修頁 **+** 把修正事實存進 builder 免疫的保留頁(沿用 #377 clarifications 機制),讓之後 fold 不會把錯的寫回來。 |
| **Q2** | 工具給 **KB chat agent + app agent**(RCA / topic-hub 等),作用於同一個 collection。 |
| **Q3** | 範本文獻**提交時拷貝存進請求**(設大小上限),不上傳進 collection、不會被檢索到。 |
| **Q4** | **馬上自動改**,不進收件匣、不設人工審核關卡(聊天發起 = 人已在場;改壞了再下一筆修正或手動改回)。 |
| **Q5 / Q6** | 免疫的「人工正確事實」改用**兩個平行資料夾**:`/clarifications/*.md`(#377)與 `/corrections/*.md`(本次);maintainer 免疫防護改擋整個資料夾**前綴**,而非單一檔名。 |
| **Q7** | 修正請求**可選帶 `target_page`**(不強制);沒帶時 maintainer 自己 `search_wiki` 找受影響頁。 |
| **Q8** | **不建 resource**;`/corrections/` 免疫頁本身就是紀錄(含提交人、內容),執行靠現有 wiki job 佇列 + 進度。 |
| **Q9** | 範本**只寫重點摘要**進免疫頁(可附「依據範本 X」一句);全文只當**本次修正的臨時輸入**(在 job payload)。 |
| **Q10 / Q11** | FE 做兩條:(a)**聊天下指令改 wiki**(走 agent 工具);(b)**KB chat 訊息上「回報有誤」按鈕** → 小視窗**盡量自動帶 context**。 |
| **Q12** | 「回報有誤」小視窗**預設空白 + 一鍵 AI 生成**:**適應式**——AI 能判斷就一次生成草稿(問 0 題),判斷不出來才做 **1–3 題**迷你 grill(硬上限 3,再多很聒噪);草稿給使用者**確認、可改**後才送出。 |
| **Q13** | collection **沒開 wiki(`use_wiki=False`)就沒有「回報有誤」按鈕**;工具遇到這種 collection 回**友善錯誤**,不自動幫忙開 wiki。 |
| **Q14** | **這次一併把 #377 clarifications 改成資料夾** `/clarifications/*.md` **+ 相容舊單檔** `/clarifications.md`(存在就讀,不強制搬)。 |
| **Q15** | `/corrections/` **按目標頁合併**:有 `target_page` → `/corrections/<slug(target)>.md`,同一頁的重複修正 append 到同檔;沒帶 → `/corrections/general.md`。maintainer 看同一頁的所有修正很集中。 |
| **Q16** | 本次 `/grill-me` 產出**只到計畫文件**;使用者確認後再 `/tdd` 實作。 |

---

## 2 · 架構:兩條路徑匯流到一個函式

```
                                            ┌── (append) ─► /corrections/<slug>.md  (原始 store;maintainer 免疫)
submit_wiki_correction(collection,          │
   instruction, target_page?, reference?) ──┤
                                            └── (enqueue) ─► WikiMaintenanceJob(op="correct")
                                                                     │  per-collection 序列化(與 fold 同佇列)
                                                                     ▼
                                                       _handle_correct  ──► run_wiki_corrector
                                                          (default_wiki_corrector_config
                                                           + correction prompt + #90 guidance)
                                                          讀 /corrections/ 當依據 → 用 write_file/edit_file
                                                          改真正的 wiki 頁(受 MaintainerWikiStore 保護:
                                                          改不動 /clarifications/、/corrections/)

Agent 路徑:  request_wiki_update 工具  ─────────────►  submit_wiki_correction
FE 路徑:    「回報有誤」→ AI 草擬 assist ─► 使用者確認 ─►  POST …/wiki/corrections ─► submit_wiki_correction
```

**關鍵**:兩條進入路徑(agent 工具、FE 按鈕)都收斂到**單一後端函式**
`submit_wiki_correction(...)`,避免邏輯分叉。

### 2.1 免疫層(#377 沿用 + 本次擴充)

- 現況(#377):`store.py` 有 `CLARIFICATIONS_PATH = "/clarifications.md"`(單檔)、
  `_is_reserved(path)`(比對單一路徑)、`MaintainerWikiStore`(對 reserved 路徑的
  `write` / `write_from_path` / `write_cas` / `delete` 靜默丟棄,讀/列開放)。
- 本次改為**前綴比對**:
  - 新常數 `CLARIFICATIONS_DIR = "/clarifications/"`、`CORRECTIONS_DIR = "/corrections/"`。
  - `_is_reserved(path)` 改判「路徑落在任一保留資料夾前綴下」**或**是舊的
    `"/clarifications.md"`(相容,Q14)。
  - `MaintainerWikiStore` 的四個寫入/刪除守衛沿用 `_is_reserved`,因此自動涵蓋兩個
    資料夾 + 舊單檔,maintainer 一律改不動。
  - 讀/列(`read` / `ls`)保持開放:maintainer 仍能把 clarifications + corrections
    當 context 讀進去。

### 2.2 corrections 檔的鍵與內容(Q15 / Q9)

- **鍵**:`target_page` 有值 → `/corrections/<slug>.md`(slug = 目標頁路徑正規化成
  安全檔名,例如 `/entities/foo.md` → `entities-foo.md`);無值 → `/corrections/general.md`。
- **append** 一段忠實紀錄:提交人、`target_page`(若有)、**修正指示原文**、
  **範本重點摘要**(附「依據範本 X」);全文範本不進頁(Q9)。header 沿用
  `_render_clarification` 同風格的區塊分隔(`\n---\n` + 粗體標題 + 內容)。
- 由 `submit_wiki_correction` 走**原始 `WikiFileStore`**(非 `MaintainerWikiStore`)寫入
  —— 人/工具路徑寫得進,maintainer 改不動,正是 #377 既有分工。

### 2.3 corrector agent(Q1 / Q2)

- 新增 `default_wiki_corrector_config`(比照 `default_wiki_maintainer_config` /
  `default_wiki_unfolder_config` 的既有「一 op 一 config」慣例)。
- toolset 沿用 maintainer 那組(`list_files` / `read_file` / `write_file` /
  `edit_file` / `delete_file` + `search_wiki` / `read_source` / …),**無 sandbox**。
- 新 correction prompt(bundled):告知「使用者回報了這則修正:{instruction};
  參考範本:{reference 全文};正確事實已記在 `/corrections/`;請找出受影響的 wiki 頁
  並修正」;`target_page` 有值時直接點名該頁。**套 #90 `with_collection_guidance`**。
- `filestore` / `files` 傳 `MaintainerWikiStore`(免疫守衛),與 maintainer 一致。

### 2.4 job / coordinator(Q4 / Q8)

- `WikiJobPayload.op` 新增 `"correct"`(現有:`fold|unfold|code_sync|code_split|
  code_card|code_finalize`)。payload 攜帶 `instruction` / `target_page?` /
  `reference?` / `actor`。
- `WikiMaintenanceCoordinator._handle` 新增 dispatch → `_handle_correct` → 呼叫
  `run_wiki_corrector`。**partition_key = collection_id**,自然與 fold 序列化
  (不會和同 collection 的 fold 打架)。
- 進度沿用既有 `status()` / `_phase_tracker`(prose op 靠 live job 計數)。

### 2.5 使用者側入口(Q10–Q12)

- **Agent 工具 `request_wiki_update`**:`agent/tools.py` 的 `_IMPLS` 註冊 impl;描述
  自動流到 FE tool picker(`builtin_tool_descriptions()`)。授權:加入
  `_WORKSPACE_TOOLS`(app 工作區 agent 通用)+ kb_chat presets。實作直接呼叫
  `submit_wiki_correction`。參數:`instruction`(必)、`target_page?`、`reference?`;
  collection 由 `ctx.collection_ids` 解析(僅 `use_wiki=True` 者;多個且未指定時,
  工具描述要求 agent 指定或回問)。遇 `use_wiki=False` 回友善錯誤(Q13)。
- **後端 route** `POST /kb/collections/{id}/wiki/corrections`(pydantic 請求/回應
  model):驗 `use_wiki=True`(否則 4xx 友善訊息)→ 呼叫 `submit_wiki_correction`。
- **AI 草擬 assist endpoint**(適應式,**串流**,Q12):輸入 = 該答案的問答全文 +
  引用的 wiki 頁(當 `target_page` 候選)+ 使用者已答的釐清(若有);輸出 =
  `{status:"draft", draft:{instruction,target_page?}}` **或**
  `{status:"needs_input", questions:[…]}`(≤3 題)。FE 端最多 loop 3 次收集答案後
  取得 draft。跑在受信任 API 層(甲),LLM 沿用該 collection 的 KB/wiki chat 模型
  (`kb.wiki.llm` 一系),遵守「always stream LLM」。

### 2.6 FE(Q10–Q13)

- **KB chat 訊息「回報有誤」按鈕**:僅在「collection `use_wiki=True` 且該答案引用了
  wiki 頁」時出現(Q13)。
- **草擬對話框**:預設空白 → 一鍵「AI 生成」→ 適應式(0–3 題)→ 顯示可編輯草稿
  (`instruction` + 自動帶入的 `target_page`)→ 使用者確認送出 → 打
  `POST …/wiki/corrections`。UX 目標:**自動帶好、少填多自動**,降低使用者惰性。
- 「聊天下指令改 wiki」不需新 FE:走既有聊天 + `request_wiki_update` 工具。

---

## 3 · 需要動到的檔案(預估)

**Backend**
- `src/workspace_app/kb/wiki/store.py` — `CLARIFICATIONS_DIR` / `CORRECTIONS_DIR`、
  `_is_reserved` 前綴化 + 相容舊單檔、slug helper。
- `src/workspace_app/kb/doc_questions.py` — `land_description_answer` 改寫入
  `/clarifications/<key>.md`(相容讀舊單檔)。
- `src/workspace_app/kb/wiki/corrections.py`(新)— `submit_wiki_correction`、
  corrections 頁 render/append、slug。
- `src/workspace_app/kb/wiki/jobs.py` — `op="correct"` + payload 欄位。
- `src/workspace_app/kb/wiki/coordinator.py` — `_handle_correct` dispatch。
- `src/workspace_app/kb/wiki/maintainer.py` — `default_wiki_corrector_config` +
  `run_wiki_corrector` + correction prompt(可置於 bundled prompts)。
- `src/workspace_app/agent/tools.py` — `request_wiki_update` impl + 註冊 + 授權清單。
- `src/workspace_app/api/kb_routes.py` — `POST …/wiki/corrections` + AI 草擬 assist
  endpoint(pydantic models)。

**Frontend**(`web/`)
- KB chat 訊息列(`AgentEntryView` / KbChat 相關)— 「回報有誤」按鈕(條件顯示)。
- 新草擬對話框元件 + `api/*` 呼叫 + TanStack Query mutation。
- `web/src/events.ts` / 型別若有新增需同步。

---

## 4 · 開放的實作細節(建置時決定,非分岔)

- **多 collection 解析**:app agent `ctx.collection_ids` 有多個 `use_wiki` collection
  時,工具描述要求指定;預設不猜。
- **範本大小上限**:訂一個常數(如 32KB)截斷 + 提示。
- **slug 正規化**:沿用 `kb/doc_id` / `_rid` 的既有安全字元策略,避免路徑注入。
- **AI 草擬的問答 context 來源**:FE 從該訊息的 citations 取 wiki 頁路徑當
  `target_page` 候選;問答全文由前端組。

---

## 5 · Flat phases(`/tdd`,一 phase 一 commit)

- **P1** 免疫層資料夾化:`store.py` 保留前綴 = `/clarifications/` + `/corrections/`
  (+ 相容舊 `/clarifications.md`);`doc_questions.land_description_answer` 改寫入
  `/clarifications/<key>.md`。**測試**:maintainer 對兩資料夾任一頁的 write/delete
  被丟棄、對一般頁正常;舊單檔仍讀得到。
- **P2** `submit_wiki_correction` 核心 + corrections 頁 render/append/slug(Q15/Q9)+
  新 op `"correct"` + `_handle_correct` + `default_wiki_corrector_config` +
  correction prompt(+ #90 guidance)。**測試**:提交 → `/corrections/<slug>.md`
  內容正確、job 入列、corrector 讀得到 corrections 當 context。
- **P3** agent 工具 `request_wiki_update`(catalog 註冊 + `_WORKSPACE_TOOLS` +
  kb_chat presets 授權;collection 解析;`use_wiki=False` 友善錯誤)。**測試**:
  工具呼叫 → `submit_wiki_correction`;無 wiki 回錯。
- **P4** route `POST …/wiki/corrections`(pydantic)+ AI 草擬 assist endpoint
  (適應式 0–3 題,串流)。**測試**:route happy/へ path;assist 能判斷→draft、
  判斷不出→≤3 題、超過 3 題不再問。
- **P5** FE:KB chat「回報有誤」按鈕(條件顯示)+ 草擬對話框(一鍵生成、迷你 grill、
  確認送出)。**測試**:vitest —— 按鈕僅 wiki 答案顯示、生成/追問/送出流程、
  空白預設。
- **P6** DoD:live check(qwen3 canned,corrector 真的改到頁 + 免疫頁沒被蓋)+
  `ruff check && ruff format --check` + `ty check`(全專案)+ `vitest` + `tsc` +
  `build` + `coverage … --fail-under=100`。

**每 phase 完成定義**:改動行為的 targeted 測試綠 + ruff/ty → commit;全套 + 100%
覆蓋率 gate 在最後(P6)一次跑齊。
