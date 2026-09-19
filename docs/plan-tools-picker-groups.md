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
- 現在的列表是**平的**：rca 的 28 條目（23 個內建 + `data-fetch`、`csv-column-summary`、`sci-plot`、`rca-tools`、
  `python-stack` 五個整套件列；數字是 `app.json` 數出來的，第一版憑記憶寫 24／19 是錯的）一路排下去；
  playground 有 `csv-column-summary:summarise` 這種指令列。

**前端分不出「整套件一列的第一方套件」和「內建」**：兩者 `package` 都是空、`external` 都是 false
（`rca-tools` 與 `exec` 在線路上長一樣）。所以分組依據得由伺服端說。

## 決定（grill，2026-09-19）

| # | 問題 | 決定 | 為什麼 |
|---|---|---|---|
| 1 | 組的三態怎麼存 | **不存、導出**：組內每列都「預設」→ 預設、都開 → 開啟、都關 → 關閉、混合 → 三個都不亮並標「混合」；按組的某一態 = 組內每一列都設成那一態。儲存仍是每 key 一個布林，後端零改動 | user：「正確」。`attached_tool_prefs` 沒有「整組」這種東西；展開後每列仍可個別調 |
| 2 | 預設展開／收合 | 打開時**全部收合**；「狀態混合的組」與「搜尋有命中的組」自動展開。收合狀態每次打開重來，不記。**P6 修訂（review 抓到）**：開或收只在兩個時刻決定——modal 開啟時（那時混合的組開）與搜尋詞改變時（有命中的組開、使用者親手收過的釋放）——中間只有使用者自己的點擊會動它；第一版每次 render 重算，混合組裡把最後一列按回一致，整組在游標下瞬間收起；手動收合又永久壓過搜尋命中，標題畫著、命中列不見。**P7 修訂（第二輪）**：狀態是兩件事——`manual`（親手開／收過的）與 `autoOpen`（上次取樣時混合的組）；換搜尋詞（trim 後真的變了）時 `manual` 只留親手**開**的（P6 把親手開的也清掉，四處文字都寫「釋放親手收的」，碼比說的寬）、`autoOpen` **重取當下**混合的組（P6 用開啟時的快照，搜尋中改到混合的組一清掉搜尋就收起）；`open = manual[id] ?? (有搜尋 ? 命中 : autoOpen 有它)` | user：「可以」。收合時標題列已有三態和項數，看得出整組狀態；混合才需要看進去 |
| 3 | 組的宇宙 | **只看天花板內的列**（伺服端回來的那幾列）。app 只授一個套件 3 個指令，組裡就 3 列、`3 項`，3 列都開就是開啟；組的三態也只套到這 3 列。**不**去拿套件完整指令清單來比 | user 擔心「partial tool 永遠打開／永遠混合」；picker 的粒度本來就是 `tools[]` 條目，天花板外的指令不會出現在這裡 |
| 4 | 分組依據 | 伺服端每列加 **`group: str`**（`ToolMeta.group` → `ItemToolState.group`）：內建 → `"builtin"`；`pkg:cmd` → 套件 id；整套件一列 → 條目自己；認不得的條目 → 條目自己。用**原始 id**不用人話標籤，fold key 不隨語系變 | 前端分不出第一方整套件列和內建（見上）；沒有第二種做法 |
| 5 | 組的名稱 | 內建那組顯示 **「核心工具」／`Core tools`**（fold id 仍是 `builtin`，不顯示）；套件組用該套件的人話標籤（指令列的 `package`，或整套件列自己的 `label`），tooltip 帶原始 id | 第一版照 user 的字叫 `builtin`；P3 真瀏覽器截圖給 user 看後他改口——組名 `builtin` 底下每列還各掛一個 #724 的來源標籤「內建／Built-in」（第一方套件列也是），同一個字疊兩層意思，「看起來很怪」。改成講這組是什麼（agent 本體的函式，非 sandbox 裡的套件）的字，和來源標籤不撞 |
| 6 | 單列的組 | **不折疊**：標題列就是那一列（沒有 chevron），三態就是那列的。**P7 補**：搜尋把多列組縮到只剩一個命中時**保留標題**（標題寫的是那列屬於哪個套件，且隨輸入忽隱忽現是版面跳動）——同一個三態出現兩次是接受的代價 | 否則同一個三態顯示兩次 |
| 7 | 順序 | `builtin` 組最前，其餘套件照 `tools[]` 第一次出現的順序；組內列照 `tools[]` 順序 | 內建最多、通常最前；其餘不重排 |
| 8 | 標題列內容 | 展開鈕（名稱 + `N 項`，`aria-expanded`）+ 三態（`role=group`，混合時三個 `aria-pressed=false` + 「混合」chip）。收合時**不**再列出效果摘要 | 三態已經是摘要；多一段字是第二個真相 |
| 9 | 搜尋 | 照舊比對列的 label／key，多比對組名（組名命中 → 整組全畫）；沒有命中列的組整組不畫；有命中的組自動展開、只畫命中的列。**P6 修訂**：搜尋中**組就是它命中的那幾列**——項數、導出狀態、組三態、reset 全對同一個集合（第一版組三態會改到被搜尋藏起來的列，reset 卻只清看得到的） | 決定 2；同一畫面上兩個「整批」動作範圍不能不同 |
| 10 | 「全部回到預設」 | 不動：仍是「目前看得到的列」全部清掉 override——組收合時組內的列仍算看得到（它們是天花板內的列，只是折起來）；搜尋中 = 命中的列（決定 9 修訂） | 語意是「重設這個 modal 現在管的東西」，不是「重設畫在螢幕上的」；`resetVisible` 現在的 `visible` 就是搜尋過濾後的全部列 |
| 11 | 混合狀態的 `default_on` 提示 | 列層級照舊（「預設：開啟／關閉」）；組層級在「預設」態不顯示提示（組內各列預設可能不同） | 不發明一個組的預設值 |
| 12 | Skills modal | **不動**（它有自己的三來源 badge 與 Apply；不在這次的請求裡） | 要的是工具那個 |

## Phases

| phase | 內容 | 驗收（先紅後綠） |
|---|---|---|
| P1 | 這份 plan | — |
| P2 | 後端 `group`：`ToolMeta.group`（預設 `BUILTIN_GROUP="builtin"`）、`picker_units` 四種條目各自填、`ItemToolState.group`、route 帶出；`web/src/api/types.ts` 加 `group: string`；`docs/contract.md` 那列的形狀補 `group` | 紅：`test_catalog` 一條斷言五種條目的 `group`（`exec`→builtin、`rca-tools:spc`→`rca-tools`、`rca-tools`→`rca-tools`、`data-fetch:grab`→`data-fetch`、`mystery`→`mystery`）；`test_tools_routes` 一條斷言 rca 的 `exec` 與 `rca-tools` 在線路上 `package`/`external` 相同（前提的對照組）而 `group` 不同 |
| P3 | 前端 `ToolsChecklist`：`groupsOf(tools)`（純函式：分組、順序、導出狀態）；折疊標題列 + 組三態 + 混合 chip；預設收合／混合與搜尋命中展開；單列組不折疊；搜尋比對組名；i18n 鍵（`tools.group.builtin`＝`builtin`、`tools.group.count`、`tools.group.mixed`、`tools.group.aria`、`tools.group.toggle`） | 紅（vitest，每條先對現在的平列表紅）：(a) 三個 group 畫三個標題、`builtin` 在最前、標題文字就是 `builtin`；(b) 預設收合：組內列不在 DOM，按標題才出現；(c) 混合的組自動展開、三個鈕都 `aria-pressed=false`、有「混合」；(d) 按組的「開啟」→ `onChange` 收到組內每個 key 都 `true`，按「預設」→ 那些 key 都不在；(e) 單列組沒有展開鈕、就是那列；(f) 搜尋只命中某組的一列 → 其他組不畫、該組展開只畫那列；(g) **部分天花板**：某套件只有 2 列、兩列都 `on` → 標題顯示開啟（決定 3 的釘子）；(h) `ToolsPickerModal.test` 既有的存檔流程全綠（第一版寫「測試先展開」——不實：modal 的 fixture 是兩個單列組，根本沒有折疊；P6 補了「兩個以上內建 → 組三態 → Save」那條） |
| P4 | 文件：`docs/subsystems/frontend.md` 那列（Tools 按鈕 → … 每列三態）補「按套件折疊、builtin 一組、組三態導出」；`docs/contract.md` `ItemToolState` 形狀補 `group` | 每句對著碼；`mkdocs build --strict` 綠 |
| P5 | 組名改「核心工具」／`Core tools`（決定 5 的修訂；只動 i18n 值、兩條測試、文件） | 既有 (a) 改斷言標題文字、(f) 改用「核心」搜尋；其餘全綠 |
| P6 | review 第一輪的修：開合改成兩個時刻決定、換搜尋詞釋放手動、搜尋中組 = 命中列、標題鈕拿掉 `aria-label`（可及名稱 = 可見文字）+ 混合寫進三態群組名、缺 `group` 的列退回平列表、`--paper-1`→`--paper-2`；後端 `flat_catalog` 的套件指令 `group` 填套件名、`ItemToolState` 用常數 | 紅：混合組內按回一致仍開、手動收合後換詞重開、搜尋中項數／狀態／組三態只算命中列、a11y 名稱含項數與混合、缺 `group` 平列表、`flat_catalog` 的 `group`；釘子：builtin 排第二的輸入仍最前、折疊內單列可調、收合且一致的組 reset、modal 兩個內建走 fold → Save；token 守衛綠 |
| P7 | 第二輪的修：換詞只釋放親手收的、`autoOpen` 換詞時重取、trim 後相同不算換詞；決定 6 補「單命中保留標題」；先列開合狀態表再改 | 紅：親手開的組經打字再刪掉仍開；搜尋中改到混合、清掉搜尋仍開；只差空白的詞不釋放；釘子：reset 在搜尋中只清命中列。三根新釘子各自在突變下獨紅 |
| P8 | 第三輪（單一問題）：72 格狀態表全對、零碼缺陷；補兩根釘子縫——「搜尋中改到一致、清掉搜尋就收」（重取混合集合是 `=` 不是 `∪`；決定：照表收）、空白守衛的「第二個空白」與「只換大小寫」；註解改「正規化後的詞沒變」 | 兩根釘子各自在突變（union／比未正規化的字串）下獨紅；一行碼＋釘子不再開一輪 |
| — | 推、draft PR#828、對最終 sha `04bffba0` 跑 CI（11/11 綠）；接著 user 要求第二部分在同一條 PR 做 | — |

`docs/migrations.md`：**不加條目**——API 多一個回應欄位、前端消費，運營方不用做事。

**不做**：組層級的儲存（決定 1）；記住收合狀態；組的效果摘要文字；Skills modal；拿套件完整指令清單來算（決定 3）。


## 第二部分：整包授權也逐指令控制（同一條 PR，2026-09-19）

### 問題

user：「我們能針對 tool command 做控制嗎？現在只有 partial tool 才有辦法。」

對著程式碼：

- `app.json` 的 `tools[]` 條目有兩種粒度：整包（`rca-tools`）或單一指令（`rca-tools:spc`，#724）。執行期
  `tooling/registry.py:_select_commands` 兩種都吃——整包展開成該套件全部指令、冒號形只取那一個。
- 但 item 的三態 pref（`attached_tool_prefs`）只在 `apps/catalog.py:_apply_tool_prefs` 被算，而它**只走
  `app.json` 的條目**：app 授整包 `rca-tools` 時，`rca-tools:spc: false` 這種 key 根本沒被看（「Keys outside the
  ceiling no-op」）。picker（`picker_units`）也因此只能給整包一列。**只有 app 一開始就寫 `pkg:cmd` 的條目才能逐指令控制。**
- 「套件有哪些指令」在 `resolve` 那一層拿不到：第一方套件是開機掃的（`self._packages`），第三方套件是每個 turn
  開頭 `resolve_item_tools` 問 host 解出來的（`api/turn_context.py:100`）；`AppCatalog.resolve` 在那之前跑，只看
  manifest 與 profile。真正知道指令清單的點只有兩個：runner（`litellm_runner.py:507 build_function_tools(packages,
  allowed=config.allowed_tools)`）與 picker route（`tools_routes.py picker_units(ceiling, [*pkgs, *external.packages])`）。
- 目前 picker 的 `effective` 是拿「turn 用的同一條 `AppCatalog.resolve`」算的（anti-drift，route docstring 明講）。
  逐指令之後這個承諾要換個地方守：兩邊共用同一個「展開 + 套 pref」的函式。

### 決定（grill，2026-09-19）

| # | 問題 | 決定 | 為什麼 |
|---|---|---|---|
| 1 | 需求範圍 | **app 授整包時，picker 仍然一個指令一列可各自開關；`app.json` 不動**。整包條目的意思不變：「這個套件現在有的全部指令」，套件下一版多一個指令，沒有 pref 就跟預設 | user：「對」。app 要寫部分指令本來就可以 |
| 2 | 判準放哪 | **一個純函式** `tooling/catalog.py:command_grants(entries, default_entries, prefs, packages)`：把天花板條目展開成指令粒度（整包 → 該套件在 `packages` 裡有的每個 `pkg:cmd`；認不得的套件保持原樣、不展開；內建與冒號條目不變），再逐指令套 pref，回傳 `(enabled, disabled)` 兩個 `pkg:cmd`／內建名的有序清單。**runner 與 picker route 都叫它**，`AppCatalog.resolve` 不動 | 判準裝在值被算出的地方——知道指令清單的只有這兩個消費者；兩邊各寫一次就是「效果由誰答」會漂的那種 |
| 3 | pref 優先序 | `prefs["pkg:cmd"]` > `prefs["pkg"]`（舊資料的整包鍵）> 預設（profile／app 的預設集含 `pkg` 或 `pkg:cmd` 就是 on）。整包鍵**繼續有效**，不遷移 | 既有 item 的 `rca-tools: false` 不能一升版就失效；`attached_tool_prefs` 存在 item 列裡，沒有 migrate route 可跑 |
| 4 | picker 寫回 | picker 只寫**逐指令鍵**；`overrideFromTools` 從列重建 override，讀到的整包鍵在第一次 Save 時自然被拆成逐指令鍵（read-modify-PUT 整張覆蓋） | 不發明「整包鍵 + 指令鍵並存」的第二套規則；決定 3 只管讀 |
| 5 | picker 的列 | 整包授權的套件：**一個指令一列**，`group` = 套件 id、`package` = 套件人話標籤（和 #724 的 `pkg:cmd` 列同形）；折疊標題的整組三態就是「整包開關」。認不得的套件（沒 build、host 解不出）：仍是整包一列（展不開）；`unavailable` 照舊 | 沿用剛做的折疊；user 那句「partial tool 永遠打開」的顧慮在這裡反過來成立：整包授權 = 全部指令都在天花板內 |
| 6 | 給模型看的「關掉的工具」（#480） | 逐指令：`disabled` 裡的 `pkg:cmd` 各一條（`picker_units` 已會描述冒號條目）；整包全關就是它全部指令各一條 | 「可請使用者開啟」的單位要和 picker 的開關一致 |
| 7 | `AgentConfig.allowed_tools` | runner 拿到的仍是條目清單；展開在 `build_function_tools` 前一步做（`_agent_for` 已同時握有 `config` 與 `packages`）。`AgentConfig` 多帶 `tool_prefs`（resolve 原樣塞入）與 `tool_ceiling`（manifest 的 `tools[]`），讓 runner 端算得出來 | `resolve` 沒有 packages；把 prefs 帶到有 packages 的地方，而不是把 packages 帶回 resolve（第三方套件在 resolve 之後才存在） |
| 8 | profile 的 `tools` 子集 | 照舊是條目粒度（可寫整包或 `pkg:cmd`）；預設集展開規則同天花板 | 不改 manifest／profile 的語言 |
| 9 | 不做 | `app.json`／profile 語法不加東西；不做整包鍵的資料遷移；Skills 不動；`external_tools` 的 host 契約不動 | — |

### Phases

| phase | 內容 | 驗收（先紅後綠） |
|---|---|---|
| P9 | 這一節 | — |
| P10 | `tooling/catalog.py:command_grants`（純函式）+ 測試 | 紅：整包 → 每個指令；認不得的套件不展開；`pkg:cmd` pref 蓋過整包鍵、整包鍵蓋過預設；profile 預設集寫整包時各指令預設 on；天花板順序保留；內建與冒號條目原樣通過；`disabled` 逐指令且與 `enabled` 不相交 |
| P11 | runner：`AgentConfig` 帶 `tool_prefs`／`tool_ceiling`（`resolve` 原樣塞入）；`_agent_for` 用 `command_grants` 算 `allowed` 給 `build_function_tools`、算 `disabled` 給 #480 段落 | 紅（`tests/api/test_turn_external_tools.py`／`test_tool_prompt.py` 那一層，走真入口 `_agent_for`）：整包授權 + `pkg:cmd: false` → 模型的工具清單少那一個、#480 段落列出它；整包鍵 `pkg: false` → 全部指令都不在、全部列在 #480；沒有 pref → 和現在逐位元相同（parity：對五個 app.json × 有／無套件，`build_function_tools` 的輸出與 master 相同） |
| P12 | picker route：整包授權展開成指令列（`group`／`package` 同 #724 形），`pref`／`effective` 由同一個 `command_grants` 算；FE 不用改（折疊 UI 已在）；`overrideFromTools` 不變 | 紅：rca item 的 `rca-tools` 從一列變 N 列、每列 `group=rca-tools`；`pref` 對整包鍵的 item 讀成每列 pinned；Save 後寫回逐指令鍵（modal 測試）；parity：route 的 `effective` 與 runner 的 `allowed` 對同一組 prefs 一致（同一函式，測試從一張表導出） |
| P13 | 文件：`docs/contract.md` 那列、`docs/subsystems/frontend.md`、`docs/plan-third-party-tools.md`／#724 提到「整包一列」的句子；`docs/migrations.md` **加一條**（行為變：既有整包鍵仍有效，但 picker 第一次 Save 會拆成逐指令鍵——運營方不用做事，但要知道） | `mkdocs --strict` 綠 |
| P14 | review（換了效果的計算點：一輪，四把鏡頭）、乾淨後推、對最終 sha 跑 CI、更新 PR body | — |

user：「同一個 PR 改好」——不另開分支，接在 P8 之後。

## 施工紀錄

- **P2**（`fe41ab62`）：`ToolMeta.group`（預設 `BUILTIN_GROUP = "builtin"`），`picker_units` 對 `pkg:cmd`／整套件／
  認不得的條目各填原始 id；`ItemToolState.group` 帶出；FE 型別 `group: string`（四個 fixture 檔補上）；
  `docs/contract.md` 那列補 `group`。先紅的兩條照計劃：catalog 五種條目、route 用 `exec` vs `rca-tools`
  當前提的對照組（線路上 `package` 都 null、`external` 都 false）。
- **P3**（`6e74831f`）：`web/src/lib/toolGroups.ts`（純函式：`groupsOf` 分組與順序、`groupState` 導出、
  `withGroupState` 整組改寫、`prefOf`）；`ToolsChecklist` 拆成 fold 標題列 + `ToolRow` + `TriState`；
  `manual` 只記使用者親手開關過的組，其餘走「混合或搜尋命中就開」；單列組直接畫列。i18n 五個鍵
  （`tools.group.builtin` 第一版兩語系都是 `builtin`，P5 改掉）。八條先紅：六條對平列表紅、兩條（單列組、全部回到預設）是釘子。
  真瀏覽器（起真後端 8241、rca item：`builtin` 23 列 + 五個單列套件）1280 與 390 各按過一輪：收合／展開／組
  「On」23 列全變／`Exec` 單獨 Off 後標題「混合」三個不亮，`scrollWidth ≤ clientWidth`；390 第一版把
  組名截成「bui…」——次要的項數與混合標記沒讓位，改成和列的 provenance chip 同一個「先縮」寫法後組名完整。
- **P4**：`docs/subsystems/frontend.md` 那列、本紀錄。`docs/migrations.md` 不加（運營方不用做事）。
- **P5**：user 看了 P3 的截圖：「builtin 改成其他字好了，下面也都是 builtin 看起來很怪」——組名和 #724 的來源標籤撞字。
  改成「核心工具」／`Core tools`（i18n 值、兩條測試、`frontend.md`、決定 5）。fold id、伺服端、`data-testid` 都不動。
- **P6**（review 第一輪，四把鏡頭並行、各自快照）：最嚴重的是回歸鏡頭抓到的 **CI 必紅**——標題列背景 `var(--paper-1)` 不存在
  （`no-undefined-tokens` 守衛），瀏覽器裡等於沒背景，我看了兩個寬度的截圖沒看出來。三把鏡頭各自撞到同兩個互動缺陷
  （混合組在游標下收起、手動收合壓過搜尋）→ 開合改成「兩個時刻決定」（決定 2 修訂）；真實性鏡頭指出組三態與 reset
  在搜尋中範圍不同 → 搜尋中組 = 命中列（決定 9 修訂）；缺陷鏡頭的 a11y（`aria-label` 蓋掉項數與混合）與滾動更新缺
  `group` 的退回。三根沒釘住的釘子（builtin 最前、折疊內單列、收合組 reset：突變後全綠）各補一條會紅的測試；(h) 的
  空話補成真測試。plan 的 24／19 是憑記憶寫的，改成數出來的 28／23。
- **P7**（review 第二輪，單一問題）：八個修法全釘住（每個突變只紅自己的釘子）、無回歸；抓到的是 P6 的釋放規則比四處文字寬
  （連親手開的都清）、清掉搜尋回到開啟時快照、reset 那半沒釘、單命中的組標題+列各一個三態。這次先把開合狀態表列出來
  （決定 2）再改；四條先紅（三條機制、一條釘子），三個突變各紅自己的。單命中保留標題寫進決定 6。
- **P8**（第三輪，單一問題）：72 格（條件 × 手勢 × 搜尋動作）真 DOM 對照只照 docstring 寫的 reference model，全對；四根釘子各恰一紅；
  溜過的突變是 `autoOpen` 改成聯集（表的「會少」那半沒釘）和守衛比未正規化的字串——各補一根，突變下各自獨紅。
  「搜尋中改到一致、清掉搜尋就收」是拍板：沒搜尋時只有混合的組才需要看進去，收起發生在刪詞那一下。

**順手看到、沒動的**：第一方整套件列（`data-fetch`、`rca-tools`…）右邊的來源標籤是「內建／Built-in」
（#724 的「不是第三方就是平台自己的」）——P5 把組名改掉之後不再和組名撞，但「核心工具」組裡每一列也各掛一個
「內建」標籤，資訊重複；要清掉得改 #724 的來源語彙或對核心列省略那個標籤，不在這次範圍。
