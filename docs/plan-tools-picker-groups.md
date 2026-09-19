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
| 4 | picker 寫回 | picker 只寫**逐指令鍵**；`overrideFromTools` 從列重建 override，讀到的整包鍵在第一次（有改動的）Save 時自然被拆成逐指令鍵（`setField` 是 JSON-Patch `replace /attached_tool_prefs`——整個欄位換掉，不是 merge；**P19 修訂**：第一版寫 PUT，實際是 PATCH replace，效果相同） | 不發明「整包鍵 + 指令鍵並存」的第二套規則；決定 3 只管讀 |
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
| P11 | runner：`AgentConfig` 帶 `tool_prefs`／`tool_ceiling`（`resolve` 原樣塞入）；`_agent_for` 用 `command_grants` 算 `allowed` 給 `build_function_tools`、算 `disabled` 給 #480 段落 | 紅（`tests/api/test_turn_external_tools.py`／`test_tool_prompt.py` 那一層，走真入口 `_agent_for`）：整包授權 + `pkg:cmd: false` → 模型的工具清單少那一個、#480 段落列出它；整包鍵 `pkg: false` → 全部指令都不在、全部列在 #480；沒有 pref → 和現在逐位元相同（parity：對五個 app.json × 有／無套件，`build_function_tools` 的輸出與 master 相同）。**P20 補**：`test_every_shipped_app_and_profile_finalizes_to_its_resolved_grant_expanded`（4 個 app × 12 個 profile × 有／無套件 = 24 格，集合相等）與 `test_every_shipped_profile_lists_its_tools_in_the_apps_ceiling_order`（5 個寫了 `tools` 的 profile 都是天花板順序） |
| P12 | picker route：整包授權展開成指令列（`group`／`package` 同 #724 形），`pref`／`effective` 由同一個 `command_grants` 算；FE 不用改（折疊 UI 已在）；`overrideFromTools` 不變 | 紅：rca item 的 `rca-tools` 從一列變 N 列、每列 `group=rca-tools`；`pref` 對整包鍵的 item 讀成每列 pinned；Save 後寫回逐指令鍵（modal 測試）；parity：route 的 `effective` 與 runner 的 `allowed` 對同一組 prefs 一致（同一函式，測試從一張表導出） |
| P13 | 文件：`docs/contract.md` 那列、`docs/subsystems/frontend.md`、`docs/plan-third-party-tools.md`／#724 提到「整包一列」的句子；`docs/migrations.md` **加一條**（行為變：既有整包鍵仍有效，但 picker 第一次 Save 會拆成逐指令鍵——運營方不用做事，但要知道） | `mkdocs --strict` 綠 |
| P14 | review（換了效果的計算點：一輪，四把鏡頭）、乾淨後推、對最終 sha 跑 CI、更新 PR body | — |

user：「同一個 PR 改好」——不另開分支，接在 P8 之後。

### P14 修訂：計算點放錯了（review 第一輪，2026-09-19，四把鏡頭並行，快照 `7f79327e`）

四把鏡頭各自用探針證實、去重後是同一組洞。根因一句話：決定 7 把「逐指令」算在 `_agent_for`，等於 **`allowed_tools` 本身還是
entry 粒度的舊答案**，其他每個讀它的人都拿到舊答案；而 resolve 之後收窄 `allowed_tools` 的三處帶著 `tool_ceiling`／`tool_prefs`
進 `_agent_for`，pin 又把收窄放寬回去。

| # | 洞（探針證實） | 鏡頭 |
|---|---|---|
| A | compaction（`allowed_tools=[]`）、sub-agent 定義、workflow step 的 `tools:` 三處在 resolve 後用 `structs.replace` 收窄，ceiling／prefs 原封帶著 → 釘 ON 的 `rca-tools:spc` 出現在只授 `read_file` 的 step 與 sub-agent 裡；#480 段落對收窄的 turn 列出整個天花板（28–30 條「已關閉、請使用者開啟」，在沒有使用者的 headless step 上）。**P20 修訂**：compaction 那半是探針帶了 packages 造成的——production 的 pre-turn compaction ctx（`chat_send.py`）是裸 `AgentToolContext`、`packages=[]`，P11 的重算（`if packages and …`）從沒碰到它；第二輪真實性鏡頭抓到 | 四把 |
| B | WUI `callTool`（`api/wui_routes.py`）與 `_wui_callable`（`agent/tools.py`）仍讀 entry 粒度 → 釘掉的 `pareto` 從頁面照樣 200 執行；反向（profile 沒給、pin ON 的指令）403 | 四把 |
| C | route 自己算預設集 `prof.tools if prof.tools else ceiling`（`[]` 是 falsy → 整個天花板），resolve 是 `is not UNSET`（`[]` = 零）→ `tools: []` 的 profile picker 全亮、runner 零；route 的 module docstring／`contract.md`「同一條 resolve」已假、`app_catalog` 參數沒人用 | 四把 |
| D | `_warn_undeclared` 拿展開後的 `wafer-history:trend` 和 `external_tools` 的鍵 `wafer-history` 比 → 每個第三方套件每次開 modal 都 WARNING「declared but not in tools[]」 | 缺陷、回歸 |
| E | 環境變數面板 `web/src/lib/envNeeds.ts` 按**列**分組 → 整包授權的套件每個指令重複一組同樣的欄位、`wantedBy`／`undeclared` 各列一次（rca 從 5 個套件名變 15 個指令名） | 缺陷 |
| F | `agent/context.py` 的 provisioning 用 entry 粒度判「要不要裝這包」（prod `prebuilt_dir=None` 沒踩到） | 符合度 |
| G | `unit_pref` 的整包鍵 fallback 對**部分授權**的 `pkg:cmd` 也生效（master 對天花板外的鍵 no-op） | 缺陷、回歸 |
| H | 天花板同時寫 `rca-tools` 和 `rca-tools:spc` → `spc` 畫兩列、建兩次 | 缺陷 |
| I | 套件和內建同名（`exec`）→ `expand_entries` 先查套件，內建那列消失 | 缺陷 |
| J | 「另外五個建構點」grep 是 6；`AgentConfig.tool_prefs` docstring「只當 fallback 重讀」不準；`picker_units`／`apps/base.py`／`apps-platform.md:108` 的「一條目一列」「ceiling 外的鍵 no-op」過期；P11「parity 表」與 P12「modal 測試」驗收欄沒出貨；`test_command_grants.py` 的「Parity with `_apply_tool_prefs`」沒拿它當 oracle；決定 4 寫 PUT，實際是 JSON-Patch `replace` | 真實性、符合度 |

**成立的（有查）**：38 列／rca-tools 10／csv 2／data-fetch 1／sci-plot 1／python-stack 整包 1，四個 rca profile 都 38（真後端＋ `WORKSPACE_TOOLS_DIR` 的真套件；`rca-tools` 不在 repo 裡，快照上驗不到）；整包鍵三處都讀得到；
`AgentConfig` 沒註冊進 specstar，沒 migrate；`build_function_tools` 的 `_select_commands` 吃 `pkg:cmd`；規則兩根釘子（優先序、分割順序）突變都紅。

#### 決定修訂

| # | 原決定 | 改成 | 為什麼 |
|---|---|---|---|
| 2、7 | runner 與 picker 各自在有 packages 的地方叫 `command_grants`；`AgentConfig` 帶 `tool_ceiling`／`tool_prefs` 讓 `_agent_for` 算 | **config 在「packages 齊了」的那一點定案**：`apps/catalog.py:finalize_tool_grants(config, packages)` 用 `command_grants` 把 `allowed_tools`／`disabled_tools` **本身**寫成指令粒度，並把 `tool_ceiling`／`tool_prefs` **用掉清空**（沒有 ceiling 的 config 原樣回傳 → 冪等、只能定案一次）。`_agent_for` 回到 master 的碼。定案的門有四扇（下表）；門之後每個讀 `allowed_tools` 的人（runner、`_wui_callable`、provisioning、authz、sub-agent clamp、compaction、sub-agent、sizing）**由構造**一致 | 洞 A／B／F 全是「讀者拿到 entry 粒度」：讀者有八個以上，生產者只有四扇門；修讀者是逐實例、修生產者是整類消失 |
| 新 10 | — | **收窄 = 定案之後的交集**，字面規則一個：`tooling/catalog.py:narrow_entries(entries, held)`——`e ∈ held` 留；裸名 `pkg` → held 裡它的每個 `pkg:cmd`（按名排序）；`pkg:cmd` 在 `pkg` 整包被持有時留；其餘丟；去重。三處共用：workflow step 的 `tools:`（`build_workflow_turn(tool_subset=)`，在 `_common` 定案之後做，`workflow_exec` 不再自己 `replace`）、sub-agent 載入時的 `clamp_tools`、`save_subagent` 的拒絕判準 | A 的 workflow 半邊在 resolve 之後、packages 之前收窄，字面比對在指令粒度的 held 上會把宣告 `rca-tools` 的 step／sub-agent 剝光；「宣告名單 ∩ 持有」三處是同一件事 |
| 新 11 | — | picker route **回去叫 `resolve_agent_config`**（master 的做法），再 `finalize_tool_grants` 同一個函式 → `effective = allowed_tools`；`default_on` = `expand_entries(profile_default_tools(slug, profile))`，而 `profile_default_tools` 是**從 `resolve` 抽出來**、resolve 自己也叫的那段（UNSET → 天花板；`[]` → 零；子集檢查） | 洞 C：「A 讀檔的方式和 B 一樣」要共用函式，不能兩份手抄 |
| 新 12 | — | `_warn_undeclared` 比對的是單位的**套件名**（`u.name.partition(":")[0]`） | 洞 D |
| 新 13 | — | 環境變數面板按**套件**折（`group`≠`builtin` 的列以 `group` 去重、標籤用 `package`），`wantedBy`／`undeclared` 也以套件計 | 洞 E：`env_needs` 是套件的屬性，一列一組是把同一份宣告畫 N 次 |
| 新 14 | — | `expand_entries` 去重（第一次出現為準）、命中內建名的條目不展開；`unit_pref` 的整包鍵 fallback **保留**（部分授權也管）——寫進文件 | H、I 一行各一；G 是拍板：存下來的「這個套件關掉」在 app 之後把授權縮成 `pkg:cmd` 時應該還算數，picker 與 runner 現在一致就好 |

#### 門（config 與 packages 相遇的地方＝要定案的生產者；grep 導出）

| 門 | packages 從哪來 | 之前 | 之後 |
|---|---|---|---|
| `api/turn_context.py:TurnContextBuilder._finalized`（`build_chat_turn`／`build_workflow_turn` 都在 `_subagent_defs` 與 `_common` 之前叫它；chat／workflow／排程／`wui/run`／goal 全走這裡） | `[*self._packages, *external.packages]` | `agent_config` 原樣進 ctx，`_agent_for` 重算 | `finalize_tool_grants`，再 `narrow_entries(tool_subset)`。**P20 修訂**：第一版把門寫成 `_common`、把 `_overhead_for` 列成受益者——sizing 只量內建（`build_tools` 跳過非內建名），看不出差別 |
| `api/wui_routes.py:wui_call_tool` | `[*bundled, *external.packages]` | `config.allowed_tools`（entry） | `finalize_tool_grants(config, available)` 再 `find_allowed_command` |
| `api/tools_routes.py` picker | `[*pkgs, *external.packages]` | 自己 `command_grants` + 自己的預設公式 | 決定 11 |
| `api/replay_loaders.py` → `health/replay.py:_agent_for(config, packages)` | `self._packages`（只有第一方——既有限制） | resolve 原樣 | `finalize_tool_grants(config, self._packages)`，重播的工具清單才和真 turn 一樣 |

不是門的：`skill_eval`（不帶 packages）、`factories.py`（只讀 model）、KB／wiki／card-drafter 六個建構點（`tool_ceiling` 空 → 原樣）。

#### 補的 phases

| phase | 內容 | 驗收（先紅後綠） |
|---|---|---|
| P15 | 規則：`finalize_tool_grants`（`apps/catalog.py`）、`narrow_entries`、`expand_entries` 去重＋內建名守衛、`profile_default_tools` 抽出來 | 紅：定案後 `allowed_tools` 是 `pkg:cmd`、ceiling／prefs 清空、再定案一次不變、沒 ceiling 原樣；`narrow_entries` 五格（留／裸名展成持有的／整包持有時留 `pkg:cmd`／不持有丟／去重）；`tools: []` 的 profile → `[]`；resolve 的 `allowed_tools`（無 pref）＝`profile_default_tools`（parity，resolve 當 oracle） |
| P16 | turn 那扇門：`_common` 定案＋`tool_subset`；`workflow_exec` 改傳 `tool_subset`；`_agent_for` 回 master；sub-agent `clamp_tools`／`save_subagent` 用 `narrow_entries`；P11 的三條 runner 測試改走真入口（`build_chat_turn` → ctx.agent_config） | 紅（沿用四把鏡頭的探針改寫）：step `[read_file]` + pin spc ON → 沒 spc；step `["rca-tools"]` + pin pareto OFF → 少 pareto；compaction 走真 `AgentCompactor.summarise` → 零套件工具、#480 零條；sub-agent `_child_context` 只授 `read_file` → 沒 spc；定義寫裸 `rca-tools` → 得到持有的指令列 |
| P17 | 另外三扇門：WUI route、picker route（決定 11、12）、replay | 紅：釘掉的指令 WUI 403、profile 沒給但 pin ON 的 200；`tools: []` picker／runner 一致；第三方不再假警告；replay 的工具清單少釘掉的那個 |
| P18 | 前端：`envNeeds.ts` 按套件折 | 紅：同套件三列 → 一組、`wantedBy` 一次、`undeclared` 一次 |
| P19 | 文件：本紀錄、`migrations.md`（補 step／sub-agent 語意與 WUI）、`contract.md`、`apps-platform.md:108`、`AgentConfig`／`picker_units`／`apps/base.py`／`find_allowed_command` 的句子、決定 4 的 PATCH、計數 6 | `mkdocs --strict` 綠；每句寫在查證之後 |
| P20 | review 第二輪（換了機制）、乾淨後推、對最終 sha 跑 CI、PR body | — |

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
- **P9**（`6a082ff1`）：第二部分的 plan 併入本檔（user：「同一個 PR 改好」）。
- **P10**（`3d1a36f0`）：`tooling/catalog.py` 的 `expand_entries`／`unit_pref`／`command_grants`（`CommandGrants`：enabled、
  disabled、default_on），九條測試先紅（import 失敗）後綠；含 user 那兩題：指令 pin 縮整包授權、舊整包鍵仍管每個指令。
- **P11**（`8818df0e`）：`AgentConfig` 加 `tool_ceiling`／`tool_prefs`（只有 `resolve` 填；另外**六**個建構點預設空 = 不展開——第一版寫「五」，是回想的；grep 是 6）；
  `_agent_for` 有 packages 且有 ceiling 時用 `command_grants` 算給 `build_function_tools` 的 allowed 與 #480 段落。
  三條在真入口 `_agent_for` 先紅。
- **P12**（`40b50927`）：route 用同一個 `command_grants`（`default_on` 取 profile 的預設集——pin 之前——不能拿
  `cfg.allowed_tools`，否則釘成關閉的內建會顯示「預設關閉」）；`picker_units(expand_entries(ceiling))`。四條先紅（含
  picker／runner 的 parity），五條第三方測試的 key 從整包改成指令列（第三方套件同樣展開）。真後端 rca item：38 列，
  `rca-tools` 10、`csv-column-summary` 2、`data-fetch`／`sci-plot` 各 1 指令、`python-stack` 整包；真瀏覽器展開
  Rca Tools 十列各有真描述、整組 Off 只翻那十列。
- **P13**（`5f81f0ab`）：`docs/migrations.md` 加 #828 條目（行為變、沒有設定、運營方不用做事但要知道整包鍵的讀法與第一次儲存會拆）；
  `contract.md`、`frontend.md`、本紀錄。
- **merge**（`7f79327e`）：master `f5fe658a`（#825）併入；衝突只在 `ToolsChecklist.tsx` 搜尋框（master 把 inline 樣式折進 `.input`、
  這邊把 `onChange` 換成 `changeSearch`），各取一半；43 條前端測試綠。
- **P14**（`83251b8b`）：review 第一輪（四把鏡頭並行，快照 `7f79327e`）——見上方「P14 修訂」。四把鏡頭去重後 10 個洞，最重的
  A／B 四把都抓到；根因是計算點放在 `_agent_for`。決策表先列完再動手。
- **P15**（`7ccfa385`）：`finalize_tool_grants`（`apps/catalog.py`，用掉 ceiling／prefs 清空，冪等）、`narrow_entries`、
  `expand_entries` 去重＋內建名守衛、`profile_default_tools` 從 `resolve` 抽出。11 條先紅（9 條 import、2 條行為：`spc` 畫兩次、
  `exec:a` 遮掉 `exec`），resolve 當 oracle 的 parity 一條。
- **P16**（`ada55cae`）：turn 那扇門——`TurnContextBuilder._finalized` 在 `_subagent_defs` 與 `_common` **之前**定案（clamp 也要讀定案後的）；
  `build_workflow_turn(tool_subset=)` 在定案之後做交集，`workflow_exec` 不再自己 `replace`；`_agent_for` 回 master（`allowed_tools`
  照讀）；`clamp_tools`／`save_subagent` 的「持有」用 `narrow_entries`。新檔 `tests/api/test_tool_grant_doors.py` 8 條走真入口
  （chat 2、workflow step 3、compaction 1、sub-agent 2），6 條先紅（2 條在舊碼上碰巧綠、留作一致性釘子）；`save_subagent` 接受
  整包名 1 條先紅；P11 的兩條 runner 測試（釘的是被拆掉的重算）換成「runner 絕不自己套 pin」的反向釘子；鄰近 17 個測試檔 + `tests/health`（14 檔）= 31 檔，411 綠、
  1 紅 = 舊 parity 測試（P17 換掉）。
- **P17**（`1b077752`）：另外三扇門——picker route 回去叫 `resolve_agent_config` 再 `finalize_tool_grants`（同一函式）、`default_on`
  走 `profile_default_tools`、`_warn_undeclared` 比套件名；WUI `callTool` 定案後才 `find_allowed_command`；replay loader 定案
  （只有第一方套件，既有限制）。5 條先紅（WUI 兩向、`tools: []`、假警告、replay）；parity 改成 5 格 pin 表 × 門當 oracle。
- **P18**（`2c5b4ab2`）：`envNeeds.ts` 按 provider 折（`pkg:cmd` 列 → `group`，標籤 `package`；任一指令開就算 live）。3 條先紅、2 條補邊界。
- **P19**：文件——`AgentConfig`／`picker_units`／`unit_pref`／`attached_tool_prefs` 的句子、`contract.md`、`apps-platform.md`、
  `frontend.md`（PATCH）、`migrations.md`（補 step／sub-agent／WUI 三個語意與整包鍵管到部分授權）、本紀錄、決定 4、P11 的「六」；
  P12 驗收欄的 modal 測試補上（`ToolsPickerModal.test.tsx`：整包鍵來的 off 列改一格後 Save 只出逐指令鍵、沒有整包鍵——
  **釘住既有行為，不是先紅**：FE 本來就從列重建）。
- **P20**：review 第二輪（四把鏡頭，快照 `8280f172`）。缺陷鏡頭兩個 HIGH：(1) `save_subagent` 接受整包名之後，**同一回合**拼進
  `subagent_defs` 的定義沒 clamp，`run_agent` 立刻用它 → 子 agent 拿到整包含 `pareto`——上一輪修法（接受整包名）打開的洞；修在值被做出的地方：
  `_child_context` 用 `narrow_entries(defn.tools, parent.allowed_tools)`（每個定義來源都經過這裡），splice 也套 `clamp_tools`。
  (2)「`_finalized` 在 `_subagent_defs` 之前」沒有測試守——突變後 438 條全綠；補一條從 builder 讀 workspace 定義的釘子。
  另：`expand_entries` 的內建名守衛用 `builtin_tool_descriptions()`（每次建 44 個 schema，實測 40 ms；每回合 83 ms、picker 48→192 ms）
  → 改 `builtin_tool_names()`（`frozenset(_IMPLS)`）；`tooling/catalog.py` 的 registry import 改成 TYPE_CHECKING，真的成為 leaf；
  DSL 驗證器的 `tools:` 檢查改用 `narrow_entries`（寫 `rca-tools:spc` 不再被拒）；WUI 對「item 釘掉」的 403 文案改成指向 tool picker。
  真實性鏡頭 13 條：compaction 故事（見表列 A 的修訂）、門名、sizing／authz 不是受益者、「唯一改變的讀法」其實兩個、`pkg:cmd` 鍵的讀法、
  replay 只有第一方、`_apply_tool_prefs` 沒 pref 時是 profile 順序、幾個計數；P11 驗收欄的全 app parity 補成測試。
  符合度鏡頭：WUI 門用定案後的清單做授權、卻把**未定案**的 config 交給 ctx → provisioning 讀 entry 粒度（prod `prebuilt_dir=None`
  沒踩到，但門沒做完）→ ctx 改帶定案後的 config；第三方套件的逐指令 pin 沒有任何測試走過門（turn 門丟掉 `external.packages`、
  picker 門只給 `pkgs`，17／28 條全綠）→ 兩條走門的第三方測試；決定 14 那個讀法補一條點名的測試；套件與內建同名時 runner 仍註冊它
  其餘指令（master 行為）→ 句子改實話。回歸鏡頭（68 個真 chat turn 的矩陣：工具名與順序 68/68 相同、#480 差 18/68 全是逐指令化；
  picker 30/68 差全是整包列換成指令列、共有列零變）另抓到兩個未宣告：sub-agent 定義也接受 `pkg:cmd`、拒絕訊息的「Available」列的是指令名
  （22→32）；env 面板對**部分授權**的組從指令名改成套件名（決定 13 的後果）→ 寫進 runbook／本紀錄。
  五條先紅（child_context、splice、schema 計數、DSL、WUI 文案）；四條是釘子、寫在修法之後或釘既有行為（WUI provisioning、第三方兩門、
  決定 14），各由突變證明守得住；連同順序釘子共十個突變，九個只紅自己那一條，決定 14 那個（MJ）紅三條——它守的那行本來就有兩條在守，
  它是第三根、點名用的。
- **P21**：review 第三輪（只審 P20，缺陷＋回歸）。同一個發現：DSL 驗證器改用 `narrow_entries` 之後，對**裸名**天花板的前綴規則把
  `rca-tools:typo`、`rca-tools:`、`exec:foo` 全放行——存檔說 saved、那個 step 跑起來什麼都沒有（跑的那半 master 也靜默，驗證器是新放寬的）。
  修法：`narrow_entries` 的前綴規則不把內建名當套件（`exec:foo` 哪裡都不算持有）；`_profile_tool_ceiling(app, profile, packages)` 有 packages
  就先 `expand_entries` 到指令粒度——`save_workflow_impl` 給 `ctx.packages`（完整）、兩條模板 route 由 `create_app` 給第一方——展開後沒有裸
  `rca-tools`，typo 自然名不到東西；解不出的套件或零指令套件仍是裸名、`pkg:x` 過得了驗證器，所以 `_finalized` 對收窄到空的條目留一行
  WARNING（typo、釘掉的都會列）。兩條先紅（內建前綴、typo 拒絕），log 那條是寫在修法之後的釘子；三個突變各紅對的測試。回歸鏡頭其餘：68 個真 turn 對第二輪 0 差；
  sub-agent 端到端只有「同回合 save 的定義」變了（就是 P20 要的）；finalize 63 ms→0.03 ms、picker 275→90 ms；寬測試 2620 綠。
  用詞：測試 docstring「44 個 schema」是一次 finalize 兩次呼叫 = 88；「22→32」是有 pin 的案例、無 pin 是 23→33；「~40 ms」量到 32–40。
- **順序**（P16 起、寫進紀錄）：定案後 `allowed_tools` 一律天花板順序；master 的 `_apply_tool_prefs` 在**沒有 pref** 時回 profile 順序。
  出貨的 profile 順序都和天花板一致，所以模型看到的工具清單不變；一個把 `tools` 寫成不同順序的 profile 會看到清單重排（集合不變）。

**順手看到、沒動的**：第一方整套件列（`data-fetch`、`rca-tools`…）右邊的來源標籤是「內建／Built-in」
（#724 的「不是第三方就是平台自己的」）——P5 把組名改掉之後不再和組名撞，但「核心工具」組裡每一列也各掛一個
「內建」標籤，資訊重複；要清掉得改 #724 的來源語彙或對核心列省略那個標籤，不在這次範圍。
