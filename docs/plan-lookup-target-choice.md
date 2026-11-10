# 請幫我查:搜尋目標由使用者選,AI 只建議

**狀態:** 計畫(grill 2026-11-10,T1–T7);尚未實作。
**來源標記:** 〔user〕= user 的原話或明確選擇;〔查證〕= 讀 `origin/master`(`14fc0ed8`)的程式碼確認的事實;
〔施工〕= 我定的實作細節,可推翻;〔指南〕= 出自公開的 UI/UX 指南,附出處。

## 1. 問題

「請幫我查」卡片的搜尋目標(`server.lookup_targets`)現在是一串 `{name, url}`〔查證〕
`config/loader.py:_check_lookup_targets`:

- **交給小幫手查時,使用者選不了目標。** 小幫手一律用「Google,沒有就清單第一個」
  (`ai_workspace_helper.builtin_errands.pick_search_target`),自己拿 `url` 代入 `{q}`〔查證〕。
  只有自己查(新分頁)時,卡片才為每個目標畫一顆「{name} search ↗」〔查證〕`OutsideLookupCard.tsx:520`。
- **AI 不知道有哪些目標。** `ask_outside` 只給關鍵字(`query`),工具說明裡沒有目標清單〔查證〕
  `agent/outside_lookup.py:ask_outside_impl`。
- **目標只能是一般網頁網址。** 開機時要求 `url` 以 `http(s)://` 開頭、含 `{q}`,其他欄位一律拒絕〔查證〕。
  可是小幫手怎麼處理那段字串(開網頁、送表單、別的)是小幫手的事〔user〕「我們把小幫手當成黑盒子」
  「沒人規定他是一個post」——後端把它當網頁網址檢查,等於替小幫手規定了做法。

## 2. 決定

| # | 決定 | 來源 |
|---|---|---|
| T1 | **用哪個目標由使用者決定,AI 只建議**——自己查和交給小幫手都一樣 | 〔user〕「這個搜尋使用者可以自己選要用哪個，ai可以建議但最終權在使用者」 |
| T2 | **選單預設選清單第一個目標,不是 AI 建議的那個**;AI 的建議標在它的選項旁。先前定的是「不預選」,查了指南後改掉(§3.3) | 〔user〕「可以」→「根據nng」;〔指南〕NN/g(§3.3) |
| T3 | **每個目標附一段說明**:這個目標搜什麼、關鍵字怎麼下。AI 用它挑目標、照它寫關鍵字 | 〔user〕「目標如何搜尋這個資訊要有 否則ai怎麼知道要怎麼挑？」 |
| T4 | **說明只有一段,也給人看**:選單裡每個目標名稱下方。所以要寫成人看得懂的句子 | 〔user〕「好」 |
| T5 | **每個目標有 `preview` 選項**:要不要畫「{name} search ↗」(在新分頁自己開)。預設要;關掉的目標只能交給小幫手 | 〔user〕「前端選項（例如是否preview)」「對 我是指那個」 |
| T6 | **`url` 只要是網址、含 `{q}`,不限 `http(s)`**,不論 `preview` 開不開 | 〔user〕「對 要換 我們變成只要求url,而非http url」 |
| T7 | **`preview` 關掉的目標,沒連小幫手時照樣列出、停用,旁邊寫原因**(「這個目標要透過小幫手查」),不隱藏 | 〔user〕「可以」 |
| T8 | AI 只能建議清單上有的目標;名字不在清單上就當沒有建議,不報錯、不出現不存在的選項 | 〔施工〕 |
| T9 | 自己查的按鈕列也標出 AI 的建議(那顆按鈕旁「AI 建議」),按哪顆仍是人決定 | 〔施工〕,同 T1/T2 |
| T10 | `lookups/` 存檔的檔頭多記一行「目標:{name}」——人實際用的那個,不是 AI 建議的(同 D13 記「實際搜尋」的原則) | 〔施工〕 |

## 3. 設計

### 3.1 設定

```yaml
server:
  lookup_targets:
    - name: Google
      url: "https://www.google.com/search?q={q}"
      description: 一般網頁搜尋。關鍵字用英文或中文的幾個詞,不要整句。
    - name: 料號查詢
      url: "partsdb://lookup?pn={q}"
      description: 公司零件資料庫,只認完整料號,例如 A123-4567。
      preview: false
```

- `name`、`url` 必填;`description`(字串)、`preview`(布林,預設 `true`)選填〔施工〕。其他欄位照舊拒絕。
- `url` 的檢查(T6):是一個網址(有 scheme,`scheme:` 開頭)且含 `{q}`。不再要求 `http(s)`。
- 沒寫 `description` 的目標,AI 和人看到的只有名字;預設的 Google 也沒有說明——後端的預設值只能是一種語言,
  卡片的字卻跟著語系走,而 AI 本來就知道 Google 是什麼〔施工〕。缺的欄位在 `create_app` 補一次
  (`whole_lookup_targets`),app 裡每個讀清單的地方拿到的都是完整的形狀〔施工〕。
- `preview: true` 而 `url` 不是 `http(s)` 時不擋:那顆按鈕交給瀏覽器開,開不開得了是設定者的責任(T6)。
- 這是**選填、預設不改行為**的欄位 → 寫進 `docs/configuration.md`;但交給小幫手的約定改了(§3.4),
  所以實作 PR 仍要有 `docs/migrations.md` 條目(要重新打包小幫手)。

### 3.2 AI(`ask_outside`)

- 工具說明列出這個部署的目標:每一筆的 `name` 與 `description`。目標在開機時就定了,所以說明在建工具時
  組好〔施工〕(`ask_outside_tool(targets)`;現在是無參數的 `function_tool`)。
- 新增選填參數 `target`:AI 建議的目標名稱。不在清單上 → 當沒建議(T8)。只在 `query` 時有意義。
- 卡片標記(`[outside-lookup]{json}`)多帶 `target`;前後端讀法的對照 fixture
  (`tests/fixtures/outside_lookup_cases.json`)一起加例子。

### 3.3 卡片

〔指南〕選一個目標是「從互斥的幾個選項選一個」:
- NN/g〈Checkboxes vs. Radio Buttons〉:「If possible, use radio buttons rather than drop-down menus」——
  選項一直看得到、方便比較。<https://www.nngroup.com/articles/checkboxes-vs-radio-buttons/>
- 同篇:「Always offer a default selection for radio button lists」。
- NN/g〈Radio Buttons: Select One by Default or Leave All Unselected?〉:主要的合理例外是預設值
  「presumptuous」(替人做了不該做的假設);為了逼人點一下而不給預設,它稱為「an unimaginative design
  safety net」。<https://www.nngroup.com/articles/radio-buttons-default-selection/>
  → 預選 AI 的建議正是 presumptuous(也違反 T1),所以預選**清單第一個**(T2)。
- Material 3 的 radio button 指南這次沒讀到內容,不引用。

畫法:
- **交給小幫手時**:一組 radio,每個選項 = 目標名稱 + 下方的說明(T4);預設選第一個(T2);
  AI 建議的那個選項旁標「AI 建議」。搜尋按鈕的字樣用**目前選的**目標名稱(「{name} search」)。
- **自己查時**:照舊每個目標一顆「{name} search ↗」,但 `preview: false` 的不畫(T5);AI 建議的那顆旁
  標「AI 建議」(T9)。說明顯示在按鈕列下方或按鈕旁〔施工〕。
- **沒連小幫手時**,`preview: false` 的目標照樣列出、停用,寫「這個目標要透過小幫手查」(T7)。
  沒有出處說停用比隱藏好——理由是:人知道有這個目標;AI 建議了它時也找得到。
- 實作時要附 1280／390 截圖,對照上面的指南(CLAUDE.md UI 規則)。

### 3.4 交給小幫手(後端 ↔ 小幫手的約定)

- `POST /a/{slug}/items/{item_id}/outside-jobs`(`HandOverIn`)多帶 `target`:人選的目標名稱。後端只接受清單上的名字,
  不在清單上 → 422〔施工〕。
- 後端把人填的關鍵字代入那個目標的 `url`(和卡片同一種編碼:`encodeURIComponent` 等價;前後端用同一份
  fixture 對答案〔施工〕),工作裡交給小幫手的是**填好的那段字串**和目標名稱(顯示用)。
  小幫手照它自己的約定處理那段字串——後端不再假設它是網頁網址(黑盒子)。
- 小幫手不再自己挑目標:`pick_search_target` 從搜尋路徑拿掉。
- 約定不相容 → `PROTOCOL` / `MIN_PROTOCOL` 從 1 升到 2〔查證〕`version.py`、`outside_jobs.py`:
  舊的小幫手會收到 426、被要求更新,而不是把一段網址當關鍵字拿去 Google。
- 推翻 plan-ext-ai D8 的「`{name}` 是小幫手會用的那個目的地(`pick_search_target`)」:`{name}` 改成人
  選的目標。

### 3.5 存檔

`lookups/` 檔頭多一行「目標:{name}」(T10)。

## 4. 推翻的舊決定

- plan-outside-lookup.md §3.3:「每筆的 `url` 必須是 `http(s)://`」→ T6。
- plan-outside-helper.md §3.4:`search` 用「卡片目的地清單裡 Google 那一筆」→ T1、§3.4。
- plan-ext-ai.md D8:按鈕的 `{name}` 用 `pick_search_target` → §3.4。

## 5. 階段

| # | 內容 | 驗收 |
|---|---|---|
| P1 | 設定:`description`、`preview`;`url` 改成任何 scheme + `{q}`;`/lookup-targets` 帶出新欄位 | 設錯開機失敗並說出哪一筆;`partsdb://…{q}` 通過、`no-scheme{q}` 拒絕 |
| P2 | AI:工具說明列出目標與說明;`target` 參數;卡片標記帶 `target`(前後端 fixture) | 不在清單上的 `target` 當沒建議 |
| P3 | 卡片:radio 選目標、預設第一個、「AI 建議」;自己查的按鈕照 `preview`;沒連小幫手時停用並說明 | 預設是第一個不是 AI 的;`preview: false` 不畫按鈕;截圖兩種寬度 |
| P4 | 交給小幫手:`target` → 後端代入 → 工作帶填好的字串;協定升 2;檔頭記目標 | 不在清單上 422;舊協定 426;檔頭寫人選的目標 |
| P5 | 小幫手:搜尋照工作給的字串處理,拿掉 `pick_search_target` | 小幫手用後端給的那一段,不自己挑 |
| P6 | 文件:`configuration.md`、`migrations.md`(要重新打包小幫手、使用者重裝);live check | 照 CLAUDE.md |
| P7 | review(conformance / veracity / defect / regression)→ CI | 照 CLAUDE.md |
