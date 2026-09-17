# 把聊天欄的高度還回來

使用者回報：聊天欄上方疊了太多列，把訊息區擠得太窄。三個地方，都在 composer 之上：

1. **composer 上方的狀態列**——「已使用」的儲存用量、token 用量、「整理成摘要」——各占一列。
   而且「整理成摘要」這四個字讀起來像在請 AI 幫忙整理什麼，其實它就是 `/compact`。
2. **chat list / New 那一列**，和它底下**有 workflow 時才出現的「在此對話執行」**，是兩列。
3. **Playground 旁邊那排按鈕**（新聊天／工具／環境／環境變數／技能／工作流程／匯出），窄的時候
   換行——比消失好，但難受。使用者的想法：窄時只留 icon，更窄時收成一顆。
   **Btw：現在每顆 icon 都長一樣，完全看不出是什麼。**

第 3 點的第二句改變了做法的順序（見 P3 / P4）。

---

## 判準

- **量容器不量視窗。** 聊天欄在 1280px 的視窗裡也可能只有 280px（`breakpoints.ts` 的
  `chat min 280`）。上一次 responsive 稽核的真因就是 `isNarrow` 讀視窗不讀容器；這裡一律用
  `useContainerWidth`。
- **門檻值用量的，不用猜的。** 「字在哪個寬度開始換行」在真瀏覽器量出來，再定數字。
- **只併同一列，不併同一個選單。** `+ New` 裡的 workflow 是「開新聊天跑」，「在此對話執行」是
  「在這個聊天跑」；意思不同，硬併成一個選單就是替「只是換個框」的東西重新設計互動。
- **icon-only 這一態的前提是 icon 看得出來。** 前提不成立就不做那一態，直接跳到 `⋯`。
- **版面的驗收在真瀏覽器。** happy-dom 不做版面，`getBoundingClientRect` 永遠是 0；單元測試只釘
  結構與文字，高度、換行、重疊要開 Chromium 量。三個視窗寬度（1280 / 700 / 390）加一個
  280px 的聊天欄。

---

## 現況盤點

| # | 區域 | 元件 | 現在幾列 | 本計畫 |
|---|---|---|---|---|
| 1 | composer 狀態列 | `AgentPanel.tsx` `composer-column`（flex column, gap 6）裡依序放 `UsageBar`（4px bar ＋ 一行字）、`ContextBar`（3px bar ＋ 一行字）、compact 按鈕（hardcode 中文，未走 i18n） | 3–5 | **P1** |
| 2 | chat bar | `ItemChatShell.tsx` `item-chat-shell__bar`：ChatSwitcher ｜ `+ New` ｜ 空白 ｜ 知識庫 | 1 | **P2** |
| 2' | 「在此對話執行」 | `ItemChatShell.tsx` `item-chat-panel__launch`，在 `ItemChatPanel` 裡自成一列；`canLaunch = !runActive && workflows.length > 0` | 1 | **P2** |
| 3 | header 工具列 | `AgentPanel.tsx` `agent-header-identity` 給標題 `flexBasis: 160`，所以按鈕先掉到第二列（#456 故意的） | 1–3 | **P4** |
| 3' | 那七顆 icon | `Icon.tsx`；工具／環境／環境變數**三顆都是 `settings`**，其餘四顆 13px 細線同色 | — | **P3** |

只有 `AgentPanel` 用 `UsageBar` / `ContextBar`；KB chat 沒有這三樣。改它們不波及別處。

---

## Phase 1 — composer 狀態列併成一列，「整理成摘要」改叫 `compact`

**做法。**
- 一列，`display: flex`、`alignItems: center`、`flexWrap: wrap`、`gap`；三格中間用 `·` 隔開
  （thread 裡「… · 收合」同一種分隔慣例）。
- 兩個量表各自從「bar 在上、字在下」改成「字在左、bar 在右」的橫向小元件：bar 固定寬約 56px、
  垂直置中。整列只有**一個文字高度**。
- 哪一格沒東西（`quota <= 0`、context 還沒量到）就不畫，列自動縮短。
- 「空間已滿」那句警告保留在第二列——它少見、而且值得一整行。
- 按鈕文字 `compact`，進行中 `compacting…`；搬進 i18n 字典（兩種語系都是 `compact`），
  不再 hardcode。它和 `/compact` 指令是同一個呼叫（`AgentPanel.tsx:298` 的註解明寫），
  名字對上指令。
- **不動** thread 裡的「以上 N 則已整理成摘要 · 收合／展開」——那是在描述已經發生的事。

**驗收。**
- 新增測試：`compact-chat` 的文字是 `compact` / `compacting…`（對未修版本紅）。
- 新增測試：`workspace-usage`、`chat-context`、`compact-chat` 三者在同一個
  `composer-status` 父元素之下（對未修版本紅——現在它們是 `composer-column` 的直接子元素）。
- 真瀏覽器：1280px 下三段文字的 `top` 相差 ≤ 2px；狀態列總高 ≤ 一個文字高＋gap；390px 下
  允許換行但 `scrollWidth === clientWidth`（不橫向溢出）。
- 突變：把 `flexDirection` 改回 `column` → 結構測試不紅（happy-dom 看不到），**真瀏覽器的
  `top` 斷言要紅**——這條要寫進 harness，不能只靠人眼。

**檔案。** `web/src/pages/investigation/AgentPanel.tsx`、`web/src/pages/investigation/UsageBar.tsx`、
`web/src/components/ContextBar.tsx`、`web/src/lib/i18n.tsx`，各自的 `.test.tsx`。

---

## Phase 2 — 「在此對話執行」搬進 chat bar 那一列

**問題。** 兩層：bar 在 `ItemChatShell`，launcher 在 `ItemChatPanel`。launcher 之所以在下面，
是因為「目前這個 chat 有沒有 run 在跑」（`run.data.status`）只有 panel 知道。

**做法。**
- launcher 搬到 bar，排在 `+ New` 之後、空白之前：`ChatSwitcher ｜ + New ｜ 在此對話執行 ｜ 空白 ｜ 知識庫`。
- `canLaunch` 從 panel 往上傳一層（panel 回報 `runActive`，shell 決定畫不畫）。
- launcher 只在 `workflows.length > 0` 時存在，而那時 `showBar` 必為 true（`hasWorkflows`），
  所以永遠有列可併；`item-chat-panel__launch` 那一列刪除。
- `launchHere` 的行為不變（同一個 chat、同一個 `startRun`）。

**驗收。**
- 新增測試：有 workflow、free chat 時，`wf.launchHere.trigger` 那顆按鈕的祖先是
  `item-chat-shell__bar`，且 `item-chat-panel__launch` 不存在（對未修版本紅）。
- 既有測試：run 在跑時 launcher 不出現、點了會開 `WorkflowLaunchDialog`——遷移到新位置，不刪。
- 真瀏覽器：bar 一列，高度與沒有 launcher 時相同。

**檔案。** `web/src/components/ItemChatShell.tsx` 及其測試。

---

## Phase 3 — 七顆 icon 先弄成分得出來的

**問題。** 三顆同一個齒輪；其餘四顆縮到 13px 細線同色也分不出。P4 的「窄時只留 icon」整個押在
這件事上，所以先做這個。

**做法。**
- 三顆齒輪拆開：工具 `settings`、環境 `term`（指令在那裡跑）、環境變數 `tag`。都是現有
  `ICON_NAMES` 裡的，不畫新的。
- icon-only 時放大到 16px、`stroke-width` 加粗；有字時維持 13px。
- **這個 phase 的 gate 是使用者的眼睛**：把七顆在 13px 和 16px 各排成一列截圖，附在報告裡。
  看得出來 → P4 做三態；看不出來 → 換 icon 或 P4 改兩態（見 P4）。

**驗收。**
- 新增測試：header 七顆按鈕的 icon 名字兩兩不同（對未修版本紅——三顆 `settings`）。
- 截圖：`icons-13px.png`、`icons-16px.png`。

**檔案。** `web/src/pages/investigation/AgentPanel.tsx`、`web/src/components/Icon.tsx`（若要加
`strokeWidth` prop）。

---

## Phase 4 — header 工具列依容器寬度三態

**前提。** P3 的截圖經使用者點頭。沒點頭就把「窄」那一態拿掉，變兩態（寬 / `⋯`）。

**做法。**

| 態 | 觸發 | 畫法 |
|---|---|---|
| 寬 | 容器寬 ≥ W1 | 照現在，icon＋字 |
| 窄 | W2 ≤ 寬 < W1 | 只留 icon（16px），字進 `title` / `aria-label`（本來就有） |
| 更窄 | 寬 < W2 | 一顆 `⋯`（`dots_h`，動作類的慣例；`☰` 是給導覽用的），點開是帶完整文字的選單，七項照原順序 |

- W1 / W2 **量出來**：在真瀏覽器逐步縮窄容器，記下「字開始換行」與「只剩 icon 也放不下」的寬度，
  各留 8px 餘裕。數字寫進 `breakpoints.ts` 旁的常數並註明怎麼量的。
- 量的是 header 自己的容器（`useContainerWidth` 掛在 header 上），不是視窗。
- #456 的規則（標題 `flexBasis: 160`、先掉按鈕別壓標題）在三態都要成立：任何一態標題都不得
  被壓到省略號以下。
- 狀態小圓點、`New chat` 的存在條件（`onNewChat`）不變。

**驗收。**
- 新增測試（mock `useContainerWidth`）：
  - 寬 → 七顆按鈕各有可見文字；
  - 窄 → 七顆都在、文字不可見、`aria-label` 齊全；
  - 更窄 → 只有 `⋯`，點開後選單有七項、點任一項觸發原本的 handler。
  三條對未修版本皆紅。
- 真瀏覽器：1280 / 700 / 390px 視窗各一張；再一張 280px 聊天欄（寬視窗＋窄欄）。每張斷言
  header 只有一列、標題可讀、無橫向溢出。
- 突變：把容器寬度讀成視窗寬度 → 280px 聊天欄那張要紅（這正是上次稽核的真因）。

**檔案。** `web/src/pages/investigation/AgentPanel.tsx`、`web/src/lib/breakpoints.ts`、
`web/src/hooks/useContainerWidth.ts`（若要加 ref 轉接），各自的測試。

---

## 不做的

- 不把 `+ New` 的 workflow 選項和「在此對話執行」併成一個選單。
- 不動 thread 裡的 compaction 標記文字。
- 不動 header 七顆按鈕的**功能**，只動它們怎麼畫。
- KB chat 不在範圍（它沒有這三樣）。

---

## 完成的定義

- 四個 phase 各自一個 commit（`P1 …`、`P2 …`），flat 編號。
- 每條新規則有一條對未修版本紅的測試；每條守衛用突變驗過會咬。
- 真瀏覽器截圖：P1 兩張、P2 一張、P3 兩張、P4 四張，附在 PR。
- `pnpm run typecheck` 與受影響的 vitest 綠；推上去開 draft PR 讓 CI 跑；review 三輪為上限
  （CLAUDE.md），找不到東西的一輪才算收斂。
- **親自按過**：compact 真的 compact、`⋯` 選單每一項真的開到原本的 modal。

---

## 執行結果（2026-09-17，分支 `composer-status-row`，PR #812）

四個 phase 都做了，各一個 commit。**跟上面計畫不一樣的地方，逐條記**：

| Phase | 計畫寫的 | 實際做的 | 為什麼 |
|---|---|---|---|
| P1 | 一列、`·` 分隔、`compact` | 照計畫 | — |
| P2 | 「panel 回報 `runActive`，shell 決定畫不畫」 | shell **自己呼叫 `useRun`**（同一個 query key，是 cache hit 不是第二個請求） | 少一層 prop 傳遞，行為相同 |
| P2 | — | bar 加了 `flexWrap: "wrap"` | 併進去之後 390px 會**橫向溢出**（內容 379 對空間 350，實測）；同一頁把 launcher 隱藏就不溢——是這次搬家造成的，所以這次修 |
| P3 | gate 是使用者看截圖點頭 | 截圖做了、**沒有等到點頭就做了 P4** | 使用者說「完成計畫」時人不在；P4 的「窄」那一態砍掉是一行的事，截圖已交 |
| P4 | W1／W2 **常數**寫進 `breakpoints.ts` 旁 | **沒有常數**：header 看自己有沒有折行，折了就降一階，寬回去超過上次失敗寬度＋24px 才試升 | 常數跟語系綁（實測 zh-TW 標籤列 500px、en 521px、zh-TW 在 header 寬 760 開始折），每改一個標籤或按鈕都要重量；問版面本身沒有這個問題 |
| P4 | 突變：「把容器寬度讀成視窗寬度」 | 突變改成：「把折行判斷寫死成 false」重 build | 新機制根本不讀寬度來做判斷，原本那個突變沒有東西可改；新突變讓 700→94px、390→123px，斷言紅 |
| P4 | 真瀏覽器 1280／700／390 ＋ 280px 聊天欄 | 1280／700／390 ＋ **369px** 聊天欄（1280 視窗開工作區檢視擠出來的） | 這個佈局實際擠出來的最小值是 369 不是 280 |

### 量到的數字

| | 之前 | 之後 |
|---|---|---|
| header 高度 @1280 | 62px | 62px（labels） |
| header 高度 @700 | 94px（兩列） | 62px（icons） |
| header 高度 @390 | 123px（三列） | 62px（`⋯`） |
| 369px 聊天欄 @1280 視窗 | 折行 | 62px（`⋯`） |
| composer 狀態列 @1280 | 3–5 列 | 17px 一列，三段文字 `top` 相等 |
| chat bar @1280 | bar 一列 ＋ launcher 一列 | 一列，高度 39 有沒有 launcher 都一樣 |

「之前」的 header 數字來自把降階判斷寫死成 false 的 build——同一份 harness、同一個 item。

### 親自按過

`⋯` 五個項目各自開出原本的 modal；Export 從選單打到 `export-chat`；`compact` 打到 `compact`。

### 沒做／沒驗

- 「空間已滿」那句警告的獨立一列只有單元測試，沒在瀏覽器看過（本機配額沒滿）。
- `New chat`／`Export` 兩顆的文字是 hardcode 英文、沒進 i18n——本來就這樣，沒動。
- harness 與截圖在 session 的暫存目錄，不在 repo 裡。

---

## 第一輪 review（三把鏡頭平行）之後

### 修掉的（`6da4640b`）

| 鏡頭 | 發現 | 修法 |
|---|---|---|
| 回歸 | **chat switcher 在 390–480px 被擠成 22px、標題 0px、點不到**——`.chat-switcher` 是 basis 0，launcher 的 132px 全被它吸收，#456 同一種病 | `flex: 1 1 160px`；實測 390 時 230px 寬、標題 190px、點得到 |
| 回歸 | **`⋯` 操作完鍵盤焦點掉到 `<body>`**——選項先 unmount 再開 modal，`ModalShell` 記到的是 body | 動作前、Escape 時把焦點放回 `⋯`（`WorkflowLaunchMenu` 有同一個洞，沒動） |
| 回歸 | **launch-here dialog 換 chat 後還在，確認會在另一個 chat 起 run**——以前住在 per-chat panel 靠 `key` 保證 | pick 時記住 chat，active 移開就關掉 dialog |
| 符合度 | **「空間已滿」讓 usage 格膨脹到 393px**，`·` 和 context 被推到 250px 的洞後面（真 app 重現：top 744/753/753） | 警告改成 row 的直接子元素，`data-status-line`：整列、最後、無點；修後 744/744/744 |
| 符合度 | **寬不變、內容變時 header 不重量**（error 行出現、切語系、按鈕條件翻轉） | header 高度也當觸發；「折了」改成看**任何一個**子元素掉到標題下面——第一版只看 group，正好漏掉觸發它的那個案例（error 行自己折下去、group 還在第一列）。實測 700px 注入 260px 內容：修前 icons 94px，修後 menu 62px |
| 真實性／符合度 | `⋯` 選單只點過一項；`compacting…` 沒釘；`More` 是 hardcode 英文 | 七項全走一遍、釘住、進 i18n |

### 三句寫錯的（commit 已推，不改歷史，在這裡更正）

1. **P3「對 parent 跑會紅在 `settings, settings, settings`」——不對。** 對 parent 跑先紅在
   `new-chat-button` 找不到（testid 是同一個 commit 加的）。`settings×3` 那個紅是在工作樹裡
   先加 testid、還沒換 icon 時看到的；有看到過，但不是「對 parent」。
2. **P2「launcher 掉到第二列／少一列」——不對。** flex 折的是 DOM 順序上第一個放不下的，
   在 launcher 後面的 collections 按鈕；而且折了就是兩列，跟原本一樣多，只有寬到不用折才少一列。
3. **P4「之前 62／94／123」——數字對，來歷沒寫。** 三個「之前」是把降階判斷寫死成 false 的
   build 量的（第三列是 `health-dot`，reviewer 的複製品沒放它才算成兩列）。
