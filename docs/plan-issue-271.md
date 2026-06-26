# Plan — Issue #271: KB chat 挑選 collection 的 UI 精進

> 隨著 collection 越來越多，KB chat 開新對話時把**全部** collection 攤平成一排 toggle
> chips 變得難用。改成：少數「最常用/最可能用」的 **pills** + 一個跟 topic-hub
> **長一樣**的 modal（可搜尋、全選、勾選）。**純前端，零後端改動。**

## 現況（grill 前的事實）

- `web/src/pages/kb/KbChatPanel.tsx:118-137` — 開新對話且 log 為空時，把 `listCollections()`
  回傳的**全部** collection 攤平成 `kb-chip` toggle 按鈕。
- 選取狀態 `selected: Set<string>`（resource_id），預設 seed = **全選**
  (`KbChatPanel.tsx:56-62`)。
- collection 選擇只在 `useKbChat` 的 `createChat("", collectionIds)` 用到
  (`useKbChat.tsx:89`)，存進 `KbChatSummary.collection_ids`。→ **collection 範圍是
  「開新對話當下」的決定**；現有對話無法改範圍（沿用，不動）。
- topic-hub 的 `CollectionsPickerModal.tsx` 綁死 `fileService`（讀寫 `collections.json`），
  有搜尋 + checklist + orphans + dirty-guard + 取消/儲存，**但沒有「全選」**，字串寫死 zh-TW。
- 既有資料：`KbCollection` 帶 `owner` / `cited` / `doc_count`；每個 chat 帶 `collection_ids`；
  `client.listChats(): KbChatSummary[]` + `qk.kb.chats` 已存在。

## 鎖定決策（逐題 grill）

1. **排序訊號 = 綜合考量**，用**字典序**（透明、可測），非加權總分。順序：
   **個人頻率 ↓ → 自己擁有的 collection 優先 → cited ↓ → doc_count ↓ → name ↑（穩定）**。
   - 個人頻率 = 在使用者的 chats（`owner === me` 優先；無 owner 時計全部）裡，該
     collection 出現在 `collection_ids` 的次數。
   - 冷啟動（無對話）→ 頻率全 0，自然 fall through 到 cited → doc_count，新使用者看到
     最常被引用/最大的。
2. **pills = 固定 top-6** 排序後捷徑；collection ≤ 6 則全顯示、不出現「更多」鈕。
   尾端一個「**更多 · N**」鈕（N = 已選總數）開 modal。被選但不在 top-6 的只反映在 N 計數。
3. **預設選取 = 出現在 pills 上的那 top-6 全部**（隱藏的非 top-6 預設不選）；modal 的
   「全選」才選到全部。小型 KB（≤6）等同今天的「全選」預設。
4. **modal 重用 = 抽出共用純呈現 `CollectionsChecklist` 元件**（搜尋 + 清單列 + 全選/清除），
   topic-hub modal 與 KB chat modal 都 render 它 → 真正「長一樣」。topic-hub 保留 file-IO
   外殼 + orphans + dirty-guard；KB chat 包一層 setState 外殼。**全選加在共用元件，topic-hub
   一併獲得。** orphans 是 topic-hub 殼層特有（KB chat 是 in-memory Set 不會有 orphan）。
5. **KB chat modal 存檔 = 即時生效 +「完成」關閉**：勾選 checkbox 立即更新共用 `Set`，pills
   同步反映；「完成」只是關閉。與 live pills 一致、無覆寫風險。（topic-hub 仍批次寫檔，footer
   按鈕本就不同；共用的只有 checklist 本體。）
6. **i18n**：共用 `CollectionsChecklist` 字串走 i18n（zh-TW 預設 + en），與近期 de-jargon/i18n
   一致；趁抽元件時把 modal 寫死的 zh-TW 收進 i18n。
7. **觸發位置不變**：pills + 更多鈕 留在 composer footer，沿用現有 gating
   (`chatId == null && empty && collections.length > 0`)。現有對話仍無 picker。
8. **純前端、零後端**。TDD（vitest）依 `feedback_fe_tdd` / `feedback_targeted_tests_then_full`。

## Flat 階段計畫（P1–P6）

- **P1 — 純排序 helper**：`rankCollectionsForPills(collections, chats, me, limit=6)`
  回傳排序後清單（字典序如上）。純函式 + vitest 全覆蓋（頻率排序、isMine、cited、doc_count、
  name tiebreak、冷啟動 fallback、owner 缺值）。
- **P2 — 共用 `CollectionsChecklist`**（純呈現）+ tests：props `{ collections, selectedIds,
  onToggle, onSelectAll, onClear, search }`；搜尋框、全選/清除、列（icon+name+doc_count+
  checkbox）、空狀態提示；i18n 字串。
- **P3 — 重構 topic-hub `CollectionsPickerModal`** 內層改 render 共用 `CollectionsChecklist`
  （保留 file-IO/orphans/dirty-guard/取消·儲存）；既有 13 測試保持綠；topic-hub 獲得全選。
- **P4 — 新增 KB chat modal 殼層** `KbCollectionsModal`（即時生效、完成關閉）包 `CollectionsChecklist`
  over in-memory Set + tests。
- **P5 — 改寫 `KbChatPanel`**：flat chip list → top-6 pills（用 P1）+「更多 · N」鈕開 P4 modal；
  預設選取 = top-6；加 `useQuery(qk.kb.chats)` 算頻率；`Set` 為單一真相來源。元件測試：pills
  顯示、預設選 top-6、更多開 modal、選隱藏的更新 N、createChat 收到聯集。
- **P6 — 文件 + 收尾**：`docs/topic-hub.md` §5.2 註記新增全選；KB chat 輕量說明；最終 gate
  （完整 vitest + `pnpm typecheck` + `vite build`）。

## Gate

逐步只跑改動到的 vitest + `pnpm typecheck`；P6 末跑完整 vitest + build。FE-only，不跑 pytest。
`feedback_gate_no_pipe_mask`：不用 pipe 遮蔽結果。
