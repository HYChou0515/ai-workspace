# 該捲的地方要捲,該一致的地方要一致

使用者回報:「Members 清單沒有 scrollbar」,並要求盤點「還有哪些該有卻沒有」。

盤點下來是**兩個不同的問題**,不該混為一談:

1. **一個真缺陷**:Members 側欄的內容會被**裁掉且搆不到**——不是「沒有捲軸」而已。
2. **一個一致性問題**:39 個會捲動的容器裡有 **30 個**用瀏覽器預設捲軸,只有 9 個用了主題化的
   `.scrollable`。這不是缺陷,是看起來不像同一個產品——而且在 macOS 上讓人以為「這裡不能捲」。

---

## 1. 定案

### 1.1 判準:看容器的高度是誰決定的

盤點時我一開始用「這個清單有沒有 `maxHeight`」當判準,**那是錯的**——它會把整頁的清單一起抓進來,
而整頁清單本來就該讓頁面捲。正確的判準只有一條:

> **容器高度固定** ⇒ 裡面必須有捲動體(側欄、popover、modal body)
> **容器高度由內容決定** ⇒ 交給頁面捲,**不要**自己開一層

實證:`GlobalLayout` 是 `height: 100vh` 的 flex column,頁面區塊是
`flex:1 + minHeight:0 + overflow:auto`(`components/GlobalLayout.tsx:34`)。所以「我的資源」等
整頁清單**已經**由頁面捲動,它們不需要、也不該再加一層。

### 1.2 不在清單上加 `maxHeight`,而是讓側欄有捲動體

被否決的做法:給 `ItemMembersPanel` 的 `list` 加 `maxHeight` + `overflowY`。

否決理由:那會在一個**已經是固定高度框**的側欄裡再造一層捲動,也就是 Baymard 明確反對的
inline scroll area / 巢狀捲動——搶走頁面滾動、把上面的列藏起來、拖曳過度敏感,而且在隱藏捲軸的
裝置上使用者根本不知道內容被裁掉。

採用的做法:讓 `MembersSidebar` 照 **`HistorySidebar` 既有的形狀**——
外層 `<aside style={sidebarStyle}>` 當框(它本來就有 `overflow: hidden`),
裡面一個 `className="scrollable"` + `flex:1` + `overflowY:auto` 的捲動體。
**一個框、一層捲動**,而且不是新發明,是抄這個 repo 已經對的那一個。

### 1.3 新的捲動區要能用鍵盤操作

`UserChip` 渲染的是 `<span>`(`components/UserChip.tsx:14`),不可聚焦。一份只有人、沒有群組的
名單因此**沒有任何可聚焦元素**,鍵盤使用者捲不動它——這是 axe 的
`scrollable-region-focusable`(WCAG 2.1.1 A)。所以捲動體要帶 `tabIndex={0}`,並給它
`role="region"` + 可讀的名稱,否則它只是個沒有身分的 tab stop。

### 1.4 `.scrollable` 只管外觀,不產生捲動

`base.css:85` 的 `.scrollable` 只設 `scrollbar-width: thin` 與 `scrollbar-color`。它**不會**讓
一個沒有 `overflow` 的元素變成可捲。兩者要一起寫,而目前 30 個地方只寫了其中一半。

⚠️ **未定案**:`.scrollable` 能不能解決「我看不出來這裡可以捲」。它讓**有顯示**的捲軸變細變主題色;
但 macOS 的浮動捲軸預設是不動就隱藏,那是 OS 行為。如果目標是「一眼看得出可捲」,需要的是
`scrollbar-gutter: stable`(目前全 repo 零使用)或漸層/陰影提示,那是另一個決定。
**這份計劃不預設答案**——P4 用真瀏覽器看過再決定要不要做。

---

## 2. Phases

### P1 — Members 側欄的內容不再被裁掉

`pages/investigation/WorkspaceShell.tsx` 的 `MembersSidebar` 自己開了一個
`<aside style={sidebarStyle}>`,而 `sidebarStyle` 帶 `overflow: hidden`(同檔 1690-1699),
裡面**沒有捲動體**。同一個 rail 的另外四個分頁都有:

| rail 分頁 | 元件 | 捲動體 |
|---|---|---|
| evidence | `EvidenceSidebar` → `SidebarFrame` | ✅ 1723 |
| search | `SearchPanel` | ✅ 209 |
| history | `HistorySidebar` | ✅ 1654 |
| **members** | **`MembersSidebar`** | **❌** |
| activity | `ActivityFeed` | ✅ 39 |

**五取四,Members 是唯一漏的**——同一條規則兩個 carrier、只有一個有,這個 repo 反覆出現的形狀。

做法:比照 `HistorySidebar`,在 aside 內加一個 `className="scrollable"`、`flex:1`、
`overflowY:auto`、`tabIndex={0}`、`role="region"` 的捲動體。

先寫會紅的測試:名單長到超過框高時,存在一個可捲動且可聚焦的容器。⚠️ 單元測試在 happy-dom 下
**量不到真實高度**,所以它只能釘住「結構在」,不能釘住「真的會捲」——後者由 P4 的真瀏覽器負責,
這一點要在測試的 docstring 裡寫明,否則它會被當成比實際更強的保護。

### P2 — 兩個共用容器先套主題捲軸(涵蓋面最大、風險最低)

- `components/ModalShell.tsx:138` — 所有走 ModalShell 的 modal body 一次到位
- `components/GlobalLayout.tsx:34` — 每一頁的頁面捲軸

這兩處合計覆蓋的畫面比其餘 28 處加起來還多,而且都只是加一個 class。

### P3 — 其餘容器,逐類決定而不是無腦全補

剩下 28 處分三類,**不是每一類都該補**:

- **面板/選單/清單**(`UserPicker` `GroupPicker` `ToolsChecklist` `CollectionsChecklist`
  `SkillsModal` `WorkflowsModal` `EnvVarsModal` `PermissionDialog` `ItemShareDialog`
  `ModelEffortPicker` `ReplayDialog`×2 `WorkflowDecisionCard` `AgentEntryView`×3
  `AgentPanel:969` `WorkspaceShell:2366` `AppDashboard` `AppNewItem` `DiagnosticsPage`)
  → 補。它們就是面板,應該和側欄長一樣。
- **資料/程式碼檢視器**(`DataGrid` `JsonlView` `rawFallback` `RecordFileRenderer`
  `SanityTable`×2 `WuiView`)→ **先問再補**。這些是內容區,細捲軸在寬表格上反而難抓;
  而 `WuiView` 是第二方內容的沙箱,套平台樣式進去可能不是我們該做的事。
- 已經有的 9 處 → 不動。

### P4 — 真瀏覽器驗證,並回答 1.4 的未定案

用 Playwright 開真的 Chromium:

1. Members 側欄放進足夠多的成員,確認**捲得到最後一列**(這是 P1 的驗收,不是單元測試)。
2. 截圖比對頁面捲軸與面板捲軸,確認 P2 之後**看起來是同一個產品**。
3. 回答:光靠 `.scrollable` 夠不夠讓人「看得出可以捲」?不夠的話,`scrollbar-gutter: stable`
   要不要做,做在哪幾處。

⚠️ 這一步不能省。這是 layout 問題,而這個 repo 已經出貨過「單元測試全綠但畫面完全沒樣式」
(#709 的 `.gauge`)。**做完 = 看得到 + 按得動。**

#### P4 實測結果(真 Chromium,41 人的名單,1280×720)

| 量測 | 值 | 意義 |
|---|---|---|
| `scrollHeight / clientHeight` | 1233 / 628 | 內容是框的兩倍,確實溢出 |
| 捲到底 | `scrollTop 605`,`atBottom: true` | 捲得到 |
| 最後一列 | `lastInsideRegion` ✅ `lastInViewport` ✅ | **搆得到了**——修好前這一列是被裁掉的 |
| 鍵盤 | `focus` 命中 `members-scroll`,PageDown → 549 | 不用滑鼠也捲得動 |

**P1 的驗收通過。** 這四個數字沒有一個是 happy-dom 量得出來的。

#### 1.4 的未定案:答案是「不做」

原本要問的是:光靠 `.scrollable` 夠不夠讓人看得出可以捲?不夠的話要不要上
`scrollbar-gutter: stable`?兩題都用量的:

- `.scrollable` **有生效**(`scrollbar-width: thin`、`scrollbar-color` 都在),但
  **`offsetWidth - clientWidth = 0`**——捲軸佔零寬度,是浮動式的,不動就不顯示。所以
  「看不出可以捲」這件事,`.scrollable` 解決不了。
- 加上 `scrollbar-gutter: stable` 之後 `gutterPx` 變成 **10**,但截圖顯示那 10px 是**空白**,
  **沒有出現任何 thumb**。它防的是內容長出來時的版面跳動,不是「看得出可以捲」。

**結論:不做。** 它花掉每個面板 10px 寬度,而換不到使用者要的東西。

**如果目標是「一眼看得出還有更多」,該做的是不依賴 OS 捲軸的提示** ——最便宜且最直接的是
在標題列顯示人數(`Members · 41`):它在沒有溢出時同樣有用,不受平台捲軸行為影響,而且正是
Baymard 建議用來取代 inline scroll area 的那個方向(告知總量而不是讓人捲著找)。
這是一個**新提案**,不在原本的 1+2 範圍內,所以留給使用者決定而不是順手做掉。

---

## 3. 已知取捨

- **P3 的資料檢視器留白**是刻意的:把平台捲軸樣式套進第二方內容(`WuiView`)可能越界,
  寬表格用細捲軸也可能更難操作。這一類我不自己決定。
- **`.scrollable` 不改變 macOS 隱藏浮動捲軸的行為。** 如果使用者的真正抱怨是「不知道可以捲」,
  P1+P2 修好的是「捲得到」與「長得一致」,不是「一眼看得出來」。那需要 1.4 的第二個決定。
- **P1 的單元測試守不住真實捲動。** 它守的是結構;真實行為由 P4 守。兩者的分工要寫進測試本身,
  否則下一個人會以為有測試就安全。
