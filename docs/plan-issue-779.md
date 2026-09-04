# #779 — modal 誤關:讓「怎麼離開一個 modal」只有一套規則

> 盤點基準:`origin/master` @ `0c40b333`。
> 起點是「點外面會自動關」,但查下去真正的問題不是那個行為本身 —— 是同一個問題在 codebase 裡有**三個不同的答案**,而其中一個是「沒有答案」。

---

## 1. 現狀(已在 master 上查證)

### 1.1 三套規則並存

| 做法 | 誰在用 | 內容 |
|---|---|---|
| **A — 全出口 guard** | `ToolsPickerModal`、`CollectionsPickerModal` | `onClose={attemptClose}`,dirty 就換成 inline 確認列。因為 guard 掛在 `onClose` 上,背景點擊**和** Escape 一起被擋 |
| **B — 只擋意外出口** | `EntityRecordModal` | `closeOnBackdrop={!editing}`。檔頭寫明:「意外的出口(背景、路由跳走)在編輯中撤掉;**Escape 和 ✕ 留著** —— 那些是刻意的,而且一個關不掉的 modal 比一份掉了的草稿更糟」 |
| **C — 沒有規則** | 其餘 24 個 | 吃 `ModalShell` 的預設值,或手刻 overlay 直接把背景 `onClick` 接到 `onClose` |

A 和 B 都是想過的,但它們對**同一個問題**給了不同答案(Escape 要不要問)。C 不是決定,是預設值漏下來的結果。

### 1.2 `closeOnBackdrop` 只有一個使用者

```
$ grep -rn "closeOnBackdrop" --include="*.tsx" web/src
web/src/components/ModalShell.tsx:25:  closeOnBackdrop = true,
web/src/components/ModalShell.tsx:42:  closeOnBackdrop?: boolean;
web/src/components/ModalShell.tsx:108:      onClick={closeOnBackdrop ? () => onClose() : undefined}
web/src/components/ModalShell.test.tsx:52:  ... closeOnBackdrop={false}>
web/src/renderers/entity/EntityRecordModal.tsx:83:      closeOnBackdrop={!editing}
```

21 個 `ModalShell` 呼叫端,**20 個沒傳**。開關存在、也有測試,但寫新 modal 的人不會知道要傳它 —— 預設值決定了 20 個 modal 的行為。

### 1.3 `DialogProvider` 不在根部

`main.tsx` 的 provider stack 是 `QueryClientProvider → LocaleProvider → FontScaleProvider → ToolCatalogProvider`。`DialogProvider` 不在裡面,而是散掛在 5 個地方(`WorkspaceShell`、`KbDocIde`、`KbWikiIde`、`KbChatsSurface`、`KbCollectionPage`)。所以 `components/` 底下的 modal（`ItemShareDialog`、`SkillsModal`…）不保證拿得到 `useDialog()`。

這是 A 用 inline 確認列、而不是用既有 `useDialog().confirm()` 的原因 —— 不是選擇,是拿不到。

### 1.4 盤點(27 個 overlay,17 個要動)

判準是**關掉這件事可不可逆**:重開就恢復原狀 = 可逆;要重打一次 = 不可逆。

#### 必改 — 有未儲存的輸入(9)

| 檔案 | 裡面裝的是什麼 | 代價 |
|---|---|---|
| `components/CardDiffReview.tsx` | Monaco 右欄 `draft` + revise 說明 `note` | workflow 的人工 gate。草稿沒了,gate 還停在那裡 |
| `components/EnvVarsModal.tsx` | 整段貼進來的 `.env` | API key 要回原處重拿 |
| `pages/AppNewItem.tsx` | 建立 item 的整張 `ItemForm` | 整張重填 |
| `pages/investigation/WorkspaceShell.tsx`（EditItemModal） | 同一張 `ItemForm` | 改既有資料,更容易改到一半停手 |
| `pages/kb/NewCollectionModal.tsx` | 名稱 / 描述 / Git URL / branch / access token | token 要重新去產 |
| `pages/kb/WikiCorrectionDialog.tsx` | 更正說明 + AI 草擬結果 + 追問的回答 | AI 草稿要重跑 |
| `pages/kb/TuneParsingModal.tsx` | 問題 / parse prompt / 已跑完的 before-after 與試答 | 整個調參 session,重建要再等好幾輪 LLM |
| `renderers/entity/EntityViews.tsx`（quick-create） | 新記錄的欄位 `draft` | 重打 |
| `pages/GroupsPage.tsx`（New group） | 群組名 + owner | 重打 |

#### 必改 — 有未送出的選擇(5)

| 檔案 | 裡面裝的是什麼 | 代價 |
|---|---|---|
| `components/ItemShareDialog.tsx` | visibility + `grants` + `groupGrants` | **靜默失敗**:關掉沒有任何提示,使用者以為權限設好了 |
| `components/PermissionDialog.tsx` | collection 版的同一組 | 同上 |
| `components/ShareChatDialog.tsx` | 選好的人與群組 | 重選 |
| `components/SkillsModal.tsx` | 每個 skill 的開關 `prefs` | 重選;清單長時特別痛 |
| `autocrud/lib/.../RefTableSelectModal.tsx` | 表格多選的 row | 吃 Mantine `<Modal>` 的 `closeOnClickOutside` 預設,是**第四套**規則 |

#### 要補完(1)

| 檔案 | 現況 | 缺什麼 |
|---|---|---|
| `renderers/entity/EntityRecordModal.tsx` | 背景已擋(`closeOnBackdrop={!editing}`) | Escape / ✕ 仍直接丟掉編輯中的內容。見 §2 對這個取捨的裁決 |

#### 順手改(2)

| 檔案 | 代價 |
|---|---|
| `components/WorkflowsModal.tsx` | zip 上傳中關掉,上傳不會停,但看不到跑完沒有、也拿不到失敗訊息 |
| `components/ManageChatsModal.tsx` | inline rename 的一行 `draft`。成本低,既然要動這批就一起 |

#### 已經做對 — 不要動(3)

- `components/ToolsPickerModal.tsx` / `components/CollectionsPickerModal.tsx` —— 做法 A,是 §4 要下沉的原型。
- `pages/kb/KbCollectionsModal.tsx` —— 每次勾選即時套用,沒有暫存狀態(檔頭已寫明 "no dirty-guard, no save")。

#### 維持現狀(7)

`pages/investigation/CommandPalette.tsx`(⌘P,零輸入成本)、`components/Dialog.tsx`(背景 = `settle(null)` = 取消,落在最安全那一邊)、`components/OnboardingModal.tsx`(背景 = 軟關閉,不是永久關)、`components/ReplayDialog.tsx`(唯讀探針)、`components/GlobalSettings.tsx`(即時套用)、`components/WorkflowLaunchDialog.tsx`(唯讀 pre-flight)、`pages/SanityTable.tsx`(唯讀單列展開)。

> 已排除、不是 modal:`ChatSwitcher` 下拉、`ModelEffortPicker` / `Popover` / `FileTree` 的 click-away 層、`AskAgentDrawer` / `ReviewDrawer` / `ViewSettingsPanel`(drawer/panel,另一種 pattern)、`ImportModeDialog`(inline 確認,沒有 backdrop)、`KbCollectionPage` 與 `AgentPanel` 的拖放提示層。
> 已從初版盤點移除:`renderers/entity/BoardView.tsx` —— master 上它的 `editing` 是**卡片內 inline 編輯**(blur / Escape 收起),不是 modal。

---

## 2. 鎖定的決策

| 決策 | 內容 |
|---|---|
| **出口分兩種** | **意外的**(背景點擊)vs **刻意的**(Escape / ✕ / Cancel)。兩種的正確處理不一樣,這是 A 和 B 的分歧點 |
| **意外的出口 → 不反應** | dirty 時背景點擊**什麼都不做**。不是彈確認 —— 對一個使用者根本沒打算觸發的動作跳出對話框,本身就是打斷 |
| **刻意的出口 → dirty 才問一次** | Escape / ✕ / Cancel 在 dirty 時問「放棄未儲存的變更?」;clean 時直接關,零摩擦 |
| **裁決 A vs B** | 採 **A 的涵蓋範圍**(Escape 也要問)+ **B 的意外/刻意之分**(背景不反應而非彈窗)。B 的理由是「一個關不掉的 modal 比一份掉了的草稿更糟」—— 這句話反對的是「Escape 也拿掉」,而 dirty guard 不是關不掉,是多問一次,確認框裡就有「放棄變更」。所以 B 的擔憂被滿足,而 Escape 誤按的風險(`CardDiffReview` 裡按 Escape 想關 Monaco 的 autocomplete 是極常見的動作)被擋住 |
| **`closeOnBackdrop` 預設翻成 `false`** | 逐個標記會再漂一次 —— 20/21 沒傳它就是證據。翻轉後預設安全,要背景關的顯式打開 |
| **Escape 保留** | ARIA APG 把 Escape 關閉列為**要求**,點擊外側只寫成選配。拿掉背景點擊不傷可及性;拿掉 Escape 會 |
| **統一走 `useDialog().confirm()`** | 不再有第二種「未儲存變更」的呈現。前提是 §4 P1 把 `DialogProvider` 提到根部 |
| **不做 `dirty` prop** | `ModalShell` 不收 `dirty`。它管的是**呈現**(backdrop / Escape / focus trap),知不知道髒是呼叫端的事。dirty 一律透過 `onClose` 接 `attemptClose` 表達 —— 一個接縫,不是兩個 |

### 出口矩陣

| 出口 | clean | dirty |
|---|---|---|
| 背景點擊 | 關 | **不反應** |
| Escape | 關 | 問一次 |
| ✕ | 關 | 問一次 |
| Cancel | 關 | 問一次 |
| 路由跳走 | 關 | 不在本次範圍(見 §5) |

---

## 3. 為什麼不是「每個 modal 自己決定」

這正是現在的狀態,而它產出了三套規則加一個 Mantine 預設。規則要能被**下一個寫 modal 的人**照到,只有兩個位置有效:預設值,和唯一的接縫。所以:

- 預設值站在安全那一邊(P1)
- dirty 只有一種表達方式(`onClose={attemptClose}`),不是 prop、不是 hook 回傳值、不是各自的 inline UI(P1)
- 新的 modal 走不到舊路(P6 的 lint)

---

## 4. Phases

### P1 — 一個接縫:`DialogProvider` 上根部 + `useDirtyClose` + 翻轉預設

**Goal.** 把「未儲存變更怎麼問」收斂成一份實作,並讓 `ModalShell` 的預設站在安全那一邊。**這個 phase 不碰任何一個有問題的 modal**,只換底座。

- `DialogProvider` 移進 `main.tsx` 的 provider stack;拆掉 5 個散落的掛載點(`WorkspaceShell`、`KbDocIde`、`KbWikiIde`、`KbChatsSurface`、`KbCollectionPage`)。`useOptionalDialog` 的存在理由消失,一併退休。
- 新 `hooks/useDirtyClose.ts`:`useDirtyClose(dirty, onClose)` → `attemptClose`。dirty 就 `await dialog.confirm({...})`,選「放棄變更」才真的關。文案走 i18n,兩個既有 key(`tools.discard` / `colpicker.discardPrompt`)合併成一組共用的。
- `ModalShell` 的 `closeOnBackdrop` 預設改 `false`。
- 那 6 個要維持背景關的顯式傳 `closeOnBackdrop`:`OnboardingModal`、`ReplayDialog`、`GlobalSettings`、`WorkflowLaunchDialog`、`SanityTable`、`KbCollectionsModal`。
- `ToolsPickerModal` / `CollectionsPickerModal` 換成 `useDirtyClose`,刪掉兩份 inline 確認列。它們的行為從「背景點擊 → 彈確認」變成「背景點擊 → 不反應」,合乎 §2 的矩陣。

**會弄紅的既有測試(預期內,要一起改)**

| 測試 | 為什麼紅 | 改成 |
|---|---|---|
| `ModalShell.test.tsx:34` "closes on backdrop click but not on panel click" | 預設翻轉 | 斷言預設**不**關;顯式 `closeOnBackdrop` 才關 |
| `EditItemModal.test.tsx:49` "closes on a backdrop click but not on a panel click" | 同上 | 斷言背景不關(P2 再補 dirty 行為) |
| `KbCollectionsModal.test.tsx:111` | 同上 | 它顯式傳 `closeOnBackdrop`,測試保留原斷言 |
| `ToolsPickerModal` / `CollectionsPickerModal` 的 `*-discard-*` testid | inline 確認列改走 `Dialog` | 改斷言 `Dialog` 的確認框 |
| `EntityRecordModal.test.tsx:114` | 不受影響(它已經是 `closeOnBackdrop={!editing}`) | 不動 |

**驗收.** `ModalShell` 預設不關背景;`useDirtyClose` 有自己的測試(clean 直接關 / dirty 問 / 選放棄才關 / 選繼續編輯不關);兩個 picker 行為照矩陣;`pnpm run typecheck` 綠。

### P2 — 未儲存輸入的 9 個接上 `useDirtyClose`

**Goal.** §1.4 第一張表的 9 個,`onClose` 全部換成 `attemptClose`。

- 每個 modal 要自己算出 `dirty`。這是逐個的工作,不是機械替換 —— 例如 `CardDiffReview` 的 dirty 是 `draft !== loaded.todo || note !== ""`,`TuneParsingModal` 的是 `question`/`guidance` 動過或 `result` 非空。
- 手刻的三個(`NewCollectionModal`、`WikiCorrectionDialog`、`GroupsPage`)這裡**先不搬到 `ModalShell`**(P5 才搬),但背景 `onClick` 先改成 `undefined`、✕ 與 Cancel 走 `attemptClose`。理由:先止血,搬家是另一件事,兩件混在一個 commit 裡沒辦法各自 revert。
- 每個都要有一條**會紅的新測試**:「有未存內容時按 Escape → 不關、跳出確認」。

**驗收.** 9 個各有一條 dirty-Escape 測試;背景點擊在 dirty 時無反應。

### P3 — 未送出選擇的 5 個接上

**Goal.** §1.4 第二張表。做法同 P2,dirty 的定義是「現在的選擇 ≠ 進來時的選擇」。

- `ItemShareDialog` / `PermissionDialog` 已經有 `next()` 組出待送的 permission,dirty 可以拿它跟 `value` 比。
- `RefTableSelectModal` 是 Mantine,不吃 `ModalShell`:顯式 `closeOnClickOutside={false}`,`onClose` 走同一個 `useDirtyClose`。**這個檔在 `autocrud/lib/` 底下** —— 動它等於動內嵌的 lib,commit 要分開,方便日後抽離。

**驗收.** 5 個各有一條 dirty-Escape 測試;`ItemShareDialog` 額外一條:選了人之後點背景,選擇還在。

### P4 — `EntityRecordModal` 補完 + 順手改的 2 個

**Goal.** 把 B 收進統一規則,並清掉剩下兩個。

- `EntityRecordModal`:`closeOnBackdrop={!editing}` 保留(它已經對了),再把 `onClose` 接成 `useDirtyClose(editing, onClose)`,讓 Escape / ✕ 在編輯中問一次。**檔頭那段註解要一起改** —— 它現在寫的是舊裁決,留著就是 §3 講的「規則有兩份」。
- `WorkflowsModal`:上傳中(`busy`)視為 dirty。
- `ManageChatsModal`:某一列在 inline rename 中視為 dirty。

**驗收.** `EntityRecordModal` 編輯中按 Escape 會問;註解與行為一致。

### P5 — 三個手刻 overlay 收斂到 `ModalShell`

**Goal.** `NewCollectionModal`、`WikiCorrectionDialog`、`GroupsPage` 的 New group 改用 `ModalShell`,規則從此只有一個位置。

- Escape 它們三個各自都處理了,所以搬家真正拿到的是**focus trap 與 focus 還原**(`ModalShell` 的 #467 那段它們照不到 —— 三個都沒有任何 `Tab` 處理),外加 z-index 回到同一條 scale 上。三份各自寫的 Escape 監聽也一併退休。
- `GroupsPage` 的 `modalOverlay` / `modalCard` 兩個 style 常數刪掉。

**驗收.** 三個都有 focus trap 測試(Tab 在 panel 內循環);`grep -rn 'role="presentation"' web/src` 只剩 `ModalShell` 與 `Dialog`。

### P6 — 防回歸

**Goal.** 讓下一個寫 modal 的人走不到舊路。

- 一條 lint / 測試:`web/src` 底下除了 `ModalShell.tsx` 與 `Dialog.tsx`,不得出現 `position: "fixed", inset: 0` 帶 `onClick` 的 backdrop。既有的 click-away 層(`Popover`、`ModelEffortPicker`、`FileTree`)要在允許清單裡 —— 它們是 dropdown 的 click-away,不是 modal backdrop。
- `docs/frontend.md`(或 `CLAUDE.md` 的 FE 慣例段)補一段:出口矩陣 + 「dirty 只透過 `onClose={attemptClose}` 表達」。

**驗收.** 新增一個手刻 backdrop 會讓測試紅。

---

## 5. 非目標

- **`beforeunload`**(關瀏覽器分頁 / 重新整理)。跟本 issue 同一個家族,但那是瀏覽器層的另一種 API 與另一種取捨,混進來會讓每個 phase 都變大。
- **草稿自動保存**。真正的解法可能是「關掉也不會丟」,但那要決定存哪、什麼時候清、跨裝置怎麼辦 —— 是一個獨立的題目。本 issue 先讓「丟掉」需要一次確認。
- **drawer / panel**(`AskAgentDrawer`、`ReviewDrawer`、`ViewSettingsPanel`)。它們不是 modal,click-away 關閉是 drawer 的正常語意。
- **路由跳走時的攔截**。`EntityRecordModal` 已經自己擋掉「開檔案把 modal 換掉」那條,但通用的 route guard 需要 router 層的 blocker,範圍不同。
- **`Dialog.tsx` confirm 本身的背景行為**。它的背景點擊 = 取消 = 最安全的結果,ARIA 建議 alert dialog 不給外側關,但誤觸的後果是「什麼都沒發生」,不值得在這裡動。

---

## 6. 風險

### 6.1 翻轉預設會改到那 6 個的行為 —— 漏一個就是「按了沒反應」

沒有資料損失,但使用者原本習慣點外面關。P1 必須把 6 個一次補齊,不能分批。**驗證方式是清單對照,不是靠測試**:測試只覆蓋有測試的那幾個。

### 6.2 dirty 算錯的兩個方向,錯法不對稱

- 算太鬆(clean 誤判成 dirty)→ 每次關都問一次 → 使用者學會無腦按「放棄」→ **guard 變壁紙**,比沒有更糟。
- 算太嚴(dirty 誤判成 clean)→ 回到現狀,靜默丟掉。

前者更危險,因為它會**訓練使用者忽略確認框**。所以每個 modal 的 dirty 都要跟「進來時的初始值」比,不是「有沒有 render 過輸入元件」。P2/P3 每一個都要有一條「什麼都沒改就按 Escape → 直接關,不跳確認」的測試 —— 這條比 dirty 那條更重要。

### 6.3 `DialogProvider` 上根部會改變既有 confirm 的行為

5 個散落的 provider 各自持有自己的 state。收成一個之後,巢狀情境(例如 `KbDocIde` 在 `WorkspaceShell` 裡)從「兩個獨立的 dialog」變成「一個」。要確認沒有地方依賴同時開兩個確認框 —— 掃 `useDialog` 的呼叫點,目前看起來都是 `await` 到底、不會並行,但這要在 P1 實際驗過。

### 6.4 `RefTableSelectModal` 在 `autocrud/lib/` 底下

那是內嵌的 lib。改它會讓日後抽離變麻煩,所以 P3 要單獨一個 commit。若之後決定 autocrud 走自己的規則,revert 一個 commit 就好。

---

## 7. 出處

- [ARIA APG — Modal Dialog Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/):Escape 關閉是規範**要求**;點擊外側只寫成「某些實作會這樣做」,是選配。
- [Material Design 3 — Dialogs](https://m3.material.io/components/dialogs/guidelines):scrim 點擊等同 Cancel,可以關掉;但「永遠要留一條讓使用者關閉的路」。
- [Radix Primitives #1997](https://github.com/radix-ui/primitives/discussions/1997):`Dialog` 預設可從外側關,`AlertDialog` 預設**不可以** —— 依風險分兩類,不是一體適用。
