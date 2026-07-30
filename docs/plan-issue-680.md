# #680 — entity 詳情 modal：三個 view 雙擊開啟

從 epic #448 收尾時分出的新需求（使用者原話：「gantt chart, workload 裡的 gantt chart, 想要雙擊及打開 issue modal」）。範圍在 `/grill-me` 逐題定案後擴為「三個 entity view 共用一個詳情 modal」。

## 定案（`/grill-me`，全程繁中）

| 題 | 定案 | 否決掉的選項與理由 |
|---|---|---|
| 範圍 | **共用** `EntityRecordModal`，三個 view 都能開 | gantt 專屬：違反「不為單一需求加特例」，且 board/table 有同一個「不離開版面看細節」的需求 |
| modal 內容 | 抽出檔案分頁的 `RecordPane` 共用；進場**讀**，按 Edit 才進表單 | 直接進編輯表單：`EntityRecordView` 檔頭記著這個錯誤已被 #453 修過一次（八個輸入框 + Monaco 裡的 markdown 原始碼是改東西的介面，不是讀東西的介面） |
| 手勢 | 三個 view **都雙擊**；table 的格子單擊即編輯**不動**，雙擊掛不可編輯的 `#N` / title 欄 | 格子改純顯示：改一個 status 從 2 步變 4 步，並拿掉「格子是 `<button>`、focus 後 Enter 可編輯」這條 a11y 路 |
| 與檔案分頁 | 各管各的；health 診斷跳轉**維持**開檔案分頁；modal 右上「在檔案開啟」 | 全面 modal 化：分頁列的 raw whole-file Edit 逃生口 modal 蓋不住，而 health 的本意正是去修壞檔 |
| URL | **不進 URL** | 此 shell 目前沒有任何 UI 狀態在 URL（連開哪個檔案分頁都不在）；只為 modal 開例外要連分頁/面板狀態一起做，會撞 #677 的 peek/pin 全域記憶 |
| 其他 | 存檔後回讀取態不自動關（409 橫幅要有地方顯示）；無上/下一筆（gantt 泳道分組下語意不明確） | — |

## 接縫

- modal state 掛 `AiYamlRenderer`（已持有 `useEntityWrite` / `users` / `refIndex` / catalog / `canWrite`，三個 renderer 都在它底下），經 `EntityViewProps.onOpenRecord?(number)` 透傳 —— 與現有 `onPatch` / `refIndex` / `canWrite` 同一條路，不另開 context。
- **零額外請求**：`EntityInstance` 已帶 `body`（`web/src/api/entities.ts`），view 手上那份 projection 列表就夠餵 modal。
- 寫入沿用 `useEntityWrite`（樂觀 + `expected_version` + 409 + `canWrite`），不另開通道（#448 §B1 單一寫入路徑）。

## P1 真瀏覽器量測（結論：不需要 fallback）

`GanttView.startDrag` 在 pointerdown 就 `e.preventDefault()`，而 PR#677 實測過「游標底下的節點被換掉時 Chromium 把點擊計數歸零、`dblclick` 完全不發」。這兩件事會不會讓 bar 的雙擊失效，**用真 Chromium 量、不用推論**（happy-dom 這類缺陷一個都抓不到）。

量測方式：playwright + 真 chromium，重現三種手勢形狀並記錄原生事件序列（含 `detail` 點擊計數）。

⚠️ 量測本身的坑：`page.mouse.down()` **永遠送 `clickCount:1`**，所以手刻兩組 down/up 得到的是 `click:1 click:1`，連對照組都測不出 `dblclick`——那是量錯不是瀏覽器行為。要用 `locator.dblclick()`（送 1 再送 2）或自帶 `{ clickCount: 2 }`。

| 形狀 | 事件序列 | `dblclick` |
|---|---|---|
| A：pointerdown `preventDefault()` + window move/up 監聽（= gantt bar） | `pointerdown:0 pointerup:0 click:1 pointerdown:0 pointerup:0 click:2 dblclick:2` | **發** |
| A + 2px 手震 | 同上 | **發** |
| B：對照組，同樣監聽但不 `preventDefault` | 多出 `mousedown` / `mouseup` | 發 |
| C：第一擊把 `<button>` 換成 `<input>`（= table 格子） | 原節點只收到 `click:1`；`click:2 dblclick:2` **落在新的 `<input>` 上** | 原節點**收不到** |

結論：

1. **gantt bar 用原生 `onDoubleClick` 即可**。`preventDefault()` 只吃掉相容性滑鼠事件（`mousedown`/`mouseup`），`click` / `dblclick` 照發，手震也不影響。不需要「在 `onUp` 裡自己數點擊」的 fallback。
2. 拖曳與雙擊天然共存：`startDrag` 的 `onUp` 在 `days === 0` 時早退，雙擊不會寫入任何東西。
3. **C 的結果是 Q5 定案的技術根據**：table 格子上的雙擊，第二擊會落在替換後的 `<input>` 上，原本的格子按鈕永遠收不到 `dblclick`。所以 table 的雙擊必須掛在不會被替換掉的欄（`#N` / title），這不是偏好問題。

## Phase（實際交付）

- **P1** 真瀏覽器 spike + 本文件（✅ 上表）
- **P2** `RecordPane` → `EntityRecordPane.tsx`；檔案分頁改用它，行為零變
- **P3** `EntityRecordModal` = `ModalShell` + `EntityRecordPane` + 409 橫幅 + 「在檔案開啟」（`useOpenFile()` 為 null 就不畫死控制項）+ 唯讀 gate。同時把 409 橫幅下沉到 `shared.tsx`（view shell 有一份、檔案分頁又長了一份 inline 的，modal 會是第三份）
- **P4** 接縫 + gantt bar 雙擊（原計畫的 P4/P5 合為一個 phase：接縫沒有第一個使用者就無法驗證）。既有的 `onOpenRecord`（開檔案分頁）誠實改名 `onOpenRecordFile`
- **P5** table `#N` 欄雙擊
- **P6** board 卡雙擊，並**刪掉 board 自己那份 modal**（見下）
- **P7** 樣式進 `web/src/styles/entity-views.css` 的 `.ev-*`（PR#487 的規矩）+ 編輯中的兩個守門（見下）

### 計畫外的三個發現

1. **board 早就有一份 record modal** —— `BoardView` 卡片的 ⋯ → Edit 自己 `ModalShell` + `EntityFileEditor`，而且**一開就是編輯表單**（#453 判定為「讀東西的錯介面」）。所以本 issue 實際上是**收斂三份重複**（view shell / 檔案分頁 / board 卡），不是新增第四份。連帶：卡片選單改成 **Open**（落在閱讀視圖，Edit 在裡面一步之遙）、**不再以寫權限為條件**（唯讀成員先前根本沒有辦法讀卡片的 body）、`EntityViewProps.onSave` 隨之作廢移除。
2. **title 欄不能承載雙擊** —— grill 時 Q5 的選項寫「`#N`／title 欄」是基於「title 不可編輯」的錯誤假設；實際上 title 是普通的 `EditableCell`（單擊即換成 input），依 P1 的量測它收不到 `dblclick`。所以 table 的開啟把手**只有 `#N` 欄**（帶 `title` 提示，且只在真的接上 opener 時才提示）。
3. **編輯中兩個靜默丟稿的出口**（真瀏覽器才看見）——表單開著時「Open file」會把介面從打字底下抽掉、backdrop 誤點會直接關掉 modal，兩者都靜默丟掉未存的編輯。修法：`EntityRecordPane` 回報 `onEditingChange`，modal 在編輯中**收起 Open file、停用 backdrop 關閉**；Esc 與 ✕ 保留（關不掉的 modal 比丟稿更糟）。

## 端對端驗證（真 Chromium，非 happy-dom）

用真的 `GanttView` / `TableView` / `BoardView` + `EntityRecordModal` 起一個拋棄式 harness（`VITE_USE_MOCK` 不需要，元件全是 props 驅動），playwright 實測：

| 檢查 | 結果 |
|---|---|
| gantt bar 雙擊 | modal 開啟 |
| table `#N` 欄雙擊 | modal 開啟 |
| board 卡雙擊 | modal 開啟 |
| table 值欄**單擊** | 只進 inline 編輯，不開 modal |
| 編輯中是否還有 Open file | 沒有（0 個） |
| 編輯中誤點 backdrop | modal 仍在 |
| console error | 無 |

明暗兩色都看過（`<html data-theme="dark">`）。harness 是拋棄式的，驗完即刪、不進 PR。

## 不在範圍

深連結 / 分享網址、上下一筆導覽、health 跳轉改 modal、table 格子互動模型重做（單擊選取 + Enter 編輯）、#681（to-many ref / gantt 相依線）、#682（留言 + @提及）。
