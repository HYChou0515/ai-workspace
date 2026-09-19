# Plan: the tool picker folds by package, built-ins under one fold, a tri-state per fold

## 問題（2026-09-19，master `e98ae9cb`）

user：「工具那個 modal 可以 by tool 折疊，並且也提供 預設／開啟／關閉 三個選項；系統 builtin 算同一個折疊，
名稱就叫 builtin。」

那個 modal 是 item 面板 Tools 按鈕開的「助理可用的工具」（`web/src/components/ToolsPickerModal.tsx` →
`ToolsChecklist.tsx`，`data-testid="tools-modal"`）。現況（對著程式碼）：

- **一列 = `app.json` 的一個 `tools[]` 條目**，每列已經有「預設／開啟／關閉」三態（`ToolPref`
  `follow` / `on` / `off`），存進 item 的 `attached_tool_prefs`（`Record<key, boolean>`，沒有 key = 跟隨預設）。
  `ToolsChecklist` 是純受控元件，`overrideFromTools` 從伺服端回的列重建 override。
- 列來自 `GET /a/{slug}/items/{id}/tools`（`api/tools_routes.py`），伺服端用 `picker_units`
  （`tooling/catalog.py:80`）把條目分成四種：內建、`pkg:cmd`（一個套件的一個指令）、整個套件一列、認不得的條目。
  每列帶 `package`（**只有 `pkg:cmd` 才填**，而且填的是人話標籤如 `Rca Tools`）與 `external`（第三方）。
- 現在的列表是**平的**：rca 的 24 條目（19 個內建 + `data-fetch`、`csv-column-summary`、`sci-plot`、`rca-tools`、
  `python-stack` 五個整套件列）一路排下去；playground 有 `csv-column-summary:summarise` 這種指令列。

**前端分不出「整套件一列的第一方套件」和「內建」**：兩者 `package` 都是空、`external` 都是 false
（`rca-tools` 與 `exec` 在線路上長一樣）。所以分組依據得由伺服端說。

## 決定（grill，2026-09-19）

| # | 問題 | 決定 | 為什麼 |
|---|---|---|---|
| 1 | 組的三態怎麼存 | **不存、導出**：組內每列都「預設」→ 預設、都開 → 開啟、都關 → 關閉、混合 → 三個都不亮並標「混合」；按組的某一態 = 組內每一列都設成那一態。儲存仍是每 key 一個布林，後端零改動 | user：「正確」。`attached_tool_prefs` 沒有「整組」這種東西；展開後每列仍可個別調 |
| 2 | 預設展開／收合 | 打開時**全部收合**；「狀態混合的組」與「搜尋有命中的組」自動展開。收合狀態每次打開重來，不記 | user：「可以」。收合時標題列已有三態和項數，看得出整組狀態；混合才需要看進去 |
| 3 | 組的宇宙 | **只看天花板內的列**（伺服端回來的那幾列）。app 只授一個套件 3 個指令，組裡就 3 列、`3 項`，3 列都開就是開啟；組的三態也只套到這 3 列。**不**去拿套件完整指令清單來比 | user 擔心「partial tool 永遠打開／永遠混合」；picker 的粒度本來就是 `tools[]` 條目，天花板外的指令不會出現在這裡 |
| 4 | 分組依據 | 伺服端每列加 **`group: str`**（`ToolMeta.group` → `ItemToolState.group`）：內建 → `"builtin"`；`pkg:cmd` → 套件 id；整套件一列 → 條目自己；認不得的條目 → 條目自己。用**原始 id**不用人話標籤，fold key 不隨語系變 | 前端分不出第一方整套件列和內建（見上）；沒有第二種做法 |
| 5 | 組的名稱 | `builtin` 組**就叫 `builtin`**（照 user 的字，不翻譯）；套件組用該套件的人話標籤（指令列的 `package`，或整套件列自己的 `label`），tooltip 帶原始 id | user 指定；其餘沿用列已在用的標籤 |
| 6 | 單列的組 | **不折疊**：標題列就是那一列（沒有 chevron），三態就是那列的 | 否則同一個三態顯示兩次 |
| 7 | 順序 | `builtin` 組最前，其餘套件照 `tools[]` 第一次出現的順序；組內列照 `tools[]` 順序 | 內建最多、通常最前；其餘不重排 |
| 8 | 標題列內容 | 展開鈕（名稱 + `N 項`，`aria-expanded`）+ 三態（`role=group`，混合時三個 `aria-pressed=false` + 「混合」chip）。收合時**不**再列出效果摘要 | 三態已經是摘要；多一段字是第二個真相 |
| 9 | 搜尋 | 照舊比對列的 label／key，多比對組名；沒有命中列的組整組不畫；有命中的組自動展開、只畫命中的列 | 決定 2 |
| 10 | 「全部回到預設」 | 不動：仍是「目前看得到的列」全部清掉 override——組收合時組內的列仍算看得到（它們是天花板內的列，只是折起來） | 語意是「重設這個 modal 現在管的東西」，不是「重設畫在螢幕上的」；`resetVisible` 現在的 `visible` 就是搜尋過濾後的全部列 |
| 11 | 混合狀態的 `default_on` 提示 | 列層級照舊（「預設：開啟／關閉」）；組層級在「預設」態不顯示提示（組內各列預設可能不同） | 不發明一個組的預設值 |
| 12 | Skills modal | **不動**（它有自己的三來源 badge 與 Apply；不在這次的請求裡） | 要的是工具那個 |

## Phases

| phase | 內容 | 驗收（先紅後綠） |
|---|---|---|
| P1 | 這份 plan | — |
| P2 | 後端 `group`：`ToolMeta.group`（預設 `BUILTIN_GROUP="builtin"`）、`picker_units` 四種條目各自填、`ItemToolState.group`、route 帶出；`web/src/api/types.ts` 加 `group: string`；`docs/contract.md` 那列的形狀補 `group` | 紅：`test_catalog` 一條斷言五種條目的 `group`（`exec`→builtin、`rca-tools:spc`→`rca-tools`、`rca-tools`→`rca-tools`、`data-fetch:grab`→`data-fetch`、`mystery`→`mystery`）；`test_tools_routes` 一條斷言 rca 的 `exec` 與 `rca-tools` 在線路上 `package`/`external` 相同（前提的對照組）而 `group` 不同 |
| P3 | 前端 `ToolsChecklist`：`groupsOf(tools)`（純函式：分組、順序、導出狀態）；折疊標題列 + 組三態 + 混合 chip；預設收合／混合與搜尋命中展開；單列組不折疊；搜尋比對組名；i18n 鍵（`tools.group.builtin`＝`builtin`、`tools.group.count`、`tools.group.mixed`、`tools.group.aria`、`tools.group.toggle`） | 紅（vitest，每條先對現在的平列表紅）：(a) 三個 group 畫三個標題、`builtin` 在最前、標題文字就是 `builtin`；(b) 預設收合：組內列不在 DOM，按標題才出現；(c) 混合的組自動展開、三個鈕都 `aria-pressed=false`、有「混合」；(d) 按組的「開啟」→ `onChange` 收到組內每個 key 都 `true`，按「預設」→ 那些 key 都不在；(e) 單列組沒有展開鈕、就是那列；(f) 搜尋只命中某組的一列 → 其他組不畫、該組展開只畫那列；(g) **部分天花板**：某套件只有 2 列、兩列都 `on` → 標題顯示開啟（決定 3 的釘子）；(h) `ToolsPickerModal.test` 既有的存檔流程全綠（列在折疊裡照樣能點到——測試先展開） |
| P4 | 文件：`docs/subsystems/frontend.md` 那列（Tools 按鈕 → … 每列三態）補「按套件折疊、builtin 一組、組三態導出」；`docs/contract.md` `ItemToolState` 形狀補 `group` | 每句對著碼；`mkdocs build --strict` 綠 |
| P5 | 推、draft PR、三把鏡頭 review（換了列表的機制：一輪；有發現就再一輪）、review 乾淨後對最終 sha 跑 CI | — |

`docs/migrations.md`：**不加條目**——API 多一個回應欄位、前端消費，運營方不用做事。

**不做**：組層級的儲存（決定 1）；記住收合狀態；組的效果摘要文字；Skills modal；拿套件完整指令清單來算（決定 3）。

## 施工紀錄

（動工後逐 phase 補。）
