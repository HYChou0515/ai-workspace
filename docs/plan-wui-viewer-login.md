# 看頁面的人用自己的登入:shared / private 兩層環境變數

> grill 2026-09-30 → 2026-10-01。每條決定標了來源:**〔user〕** = user 逐題定案;
> **〔預設〕** = 我自己定、列出來給推翻的實作層決定。沒有標的是推論,後面附理由。

## 問題

WUI deploy 之後,看頁面的人按按鈕、頁面 `callTool`,tool 拿到的環境變數只有兩個來源
(`wui_routes.py:536`):

```
{ **IRequestEnv.env_for(request), **item.env_vars }   ← item 贏
```

「登入」只存在於 Env 面板的 `IEnvProvider` 按鈕,換到的值填進 **item 的 `env_vars`**——
item 上一份、所有參與者明文讀得到、只有 `write_meta` 打得開面板。所以:

1. 看頁面的人**沒地方登入**(沒有 `write_meta`)。
2. 就算讓他登入,值只能放 item 上 = 把他的憑證分給所有人。
3. 合併方向寫死 item 贏,owner 沒辦法說「這個 key 用看的人自己的」。

## 機制

### 兩層

| 層 | 存在哪 | 誰讀得到 | 誰寫 |
|---|---|---|---|
| **shared** | item 的 `env_vars`(**不動**) | 所有能開這個 item 的人(`read_meta`,今天就是) | `write_meta` 的人,Env 面板手 key 或登入 |
| **private** | 新的私人表,鍵 = (user, item) | **只有本人**;平台只在派送 tool 時讀 | 本人手 key、本人登入、`IRequestEnv.env_for` 自動寫入 |

- 私人值**不能**放 item 上:item 欄位對 `read_meta` 不遮蔽回傳(`tests/api/test_item_env_vars.py`
  釘著),放上去小華的 token 小明 `GET` 就拿到。〔user,Q3 前的討論〕
- 存**伺服器**不存 localStorage。localStorage 的版本被否決,因為 pod 死掉後被
  `turn_reclaim_sweeper` 接手重跑的 turn 拿不到值。〔user〕
- 鍵是 **(user, item)** 不是全站一份:`_tool_env` 把整份 env **無差別**交給每一支 tool(#750 記過),
  全站一份 = 小華為 A item 登入的 token 送進 B item 裡別人上架的第三方 tool。〔user,Q9〕
- private 層同一個 key **最後寫的贏**,不記來源、不排優先序。`IRequestEnv` 每次請求都重寫,所以它
  提供的 key 自然永遠是最新的自動值;手 key / 登入只對它**不**提供的 key 會留著。〔user,Q5〕
  (我原本提「手動登入贏過自動」被否決:手動多出來的能力只有「填一個不是從自己請求來的值」,
  那就是冒用。)

### 政策:每個 key 一種,放 item 上

| 政策(內部名) | 畫面文字 | 規則 |
|---|---|---|
| `shared > private`(**預設**) | 用共用值 | shared 有值用 shared,沒有才用 private |
| `private > shared` | 各人可改用自己的 | private 有值用 private,沒有才用 shared |
| `private only` | 各人自己填 | 只用 private |

〔user〕逐 key、三種、名字用 shared/private(不用 public——repo 裡 public = 沒設權限的 item)。

- **政策只決定值從哪一層拿,不代表必填。** 缺值 = 這個 key 不傳給 tool,跟今天沒設一樣;
  必填是 tool `env.json` 的 `required`,而 #750 定案它不擋。〔user〕
- **預設 `shared > private` = 今天那行 `{**request_env, **item_env}`,升級不改變任何既有行為。**〔user〕
  item 上沒有的 key(例如 `IRequestEnv` 多帶的 `SSO_TOKEN`)照樣傳下去——那是同一條規則的結果,
  不是第二條規則。
- 政策只有 `write_meta` 能設;頁面作者(`edit_content`)改不到,擋掉「參與者自己寫一頁、把 key 改成
  用 shared」。〔user〕
- 儲存:**不改 `env_vars: dict[str, str]`**,政策是 item 上並排的另一個欄位。〔user〕

### 誰的 private

| 路徑 | 用誰的 private | 來源 |
|---|---|---|
| 聊天送出 | 送出的人 | 〔user〕 |
| WUI `callTool` | 按的人 | 〔user〕 |
| 頁面按鈕起的 `wui/run` | **按的人**;帳照舊記 owner(#805) | 〔user,Q6〕 |
| goal driver 續跑、`turn_reclaim` 重跑 | 原本那一輪的作者 | 〔預設〕 |
| 排程 | **綁定的人**;沒人綁定 → shared + `env_without_request` | 〔user,Q7/Q8〕 |
| WUI build | **沒有人的**,只拿 shared | 〔#788 沿用〕 |

`wui/run` 今天是 `captured_user=owner`(`wui_routes.py:441`)。「帳記在誰名下」和「用誰的身分」拆開:
記帳不動,身分換成按的人——否則按一下別人頁面的按鈕就用了別人存的 private。

build 只拿 shared:`dist/` 會進永久儲存、組進每個看這頁的人拿到的文件,bundler 會把 env 烤進產出物
(Vite `VITE_*`)。判準:**產出回給一個人 → 可用他的 private;變成共用成品 → 只能 shared。**

### 排程:本人按了才算

`schedules.json` 誰都能寫(`edit_content`、頁面、agent 的 `exec`),「誰宣告的」沒有可靠答案,
所以用誰的身分**不能由寫檔的人決定**。〔user〕

- 綁定 = 本人按「用我的身分執行」。紀錄存平台自己的表,不在檔案裡。
- 需要 `execute`(角色階梯上最低是 Collaborator,`itemPermission.ts:39`)。〔user〕
- **一個排程只綁一個人**;別人可取代,取代前確認、取代後通知原綁定人。〔user,Q8〕
  (每人各綁 = N 倍查詢,是 `plan-wui.md`「到貨」一節算過的放錯位置。)
- **排程內容一改,綁定就失效**,通知原綁定人。`trigger_id_for`(`user_schedules.py:368`)本來就從
  folder / run / payload / when 導出,內容變 = key 變,綁定掛在 key 上自然失效。擋「先讓小明綁無害的,
  再換成查薪資」。〔user〕
- 沒人綁定:shared + `env_without_request`。後者**不寫進任何地方**(它是 service account,不屬於任何人;
  寫進 shared 所有參與者就讀到),在那次 run 裡站 private 層的位置照政策合併。〔user,B1〕
- ⚠️ 同 item 兩列內容完全一樣的排程會撞成同一個 key(`trigger_id_for` 的 docstring:故意的)。
  在「一個排程一人」底下不是問題;將來若要每人各寫一份,內容要帶區分欄位。

### 知情不擋

**同一個 item 裡能執行程式的人,可以讀到別人的 private 值。** uid 依 item 分配
(`uid_base + xxhash(item_id)`),不依人;tool 跑的那幾秒同 uid 的 process 讀得到
`/proc/<pid>/environ`,tool 本身也能把 env 寫進 workspace。private 防得住**介面**上的其他人,
防不住同 item 的 `execute` / `use_terminal`。**只寫進文件,UI 不加提示。**〔user,Q13 選 A〕
要真的擋,只有「帶 private 的 tool 換一個依人的 uid」,依賴預設關閉的 `sandbox.isolation`。

## UI

### Env 面板:兩個分頁

| 分頁 | 內容 | 誰能改 |
|---|---|---|
| **只有我**(預設打開) | 你的 private 值;每列標「使用中:你的 / 共用值」;值遮罩 + 👁 | 本人 |
| **所有參與者** | 今天的面板 + 每列政策 radio | `write_meta`;其他人整頁唯讀並寫明原因 |

依據:Postman 放棄了「一列兩個可編輯值」(initial/current),改成一個值、預設只給自己、分享是明確動作,
且雲端排程只用共用值——跟 Q7 同一條;VS Code 用 User / Workspace 分頁一次只編輯一層。〔user〕

- 兩個分頁**各自儲存**,不會一顆鈕存兩層。
- 「只有我」每一列的長相由政策決定:`shared > private` 且 shared 有值 → 不能填、寫「這個變數固定用共用值」;
  另兩種 → 可以填。
- 缺值只在 tool 宣告 `required` 且生效值是空的、而且**看的人自己填得進去**時提示;用「登入 ERP」/
  「需要設定 MAP_KEY」,**不寫「還缺 N 個」**(看不出是什麼)。系統名用 `IEnvProvider` 的名字,不用 key 名。〔user〕
- Env 按鈕:今天只給 `write_meta`;改成有 `converse` 或 `execute` 的人都看得到(他們需要「只有我」)。〔預設〕

### tool 下拉選單 → 依 tool 分段的可收合清單

〔user〕以 tool 為主軸(我提的「依變數排」被否決:tool 才是使用者想事情的單位)。拿掉下拉選單
(收合時看不出哪支要處理、一次只看一支),換成每支 tool 一段、常駐搜尋框。

| 狀態 | 符號 | 顏色 | 文字 | 預設 | 排序 |
|---|---|---|---|---|---|
| 缺必填 | ◆ | 警示(琥珀,**不是錯誤紅**) | 缺 1 個必填 | 展開 | 1 |
| 只缺選填 | ◇ | 中性 | 2 個選填未設 | 收合 | 2 |
| 已就緒 | ✓ | 成功 | 已就緒 | 收合 | 3 |

- 符號 + 顏色 + 文字三條線索(Carbon status indicator;WCAG 1.4.1)。琥珀不是紅:#750 的 `aria-invalid`
  永遠 `false`,必填是提示不是閘門。
- **沒有 `env.json` 的 tool,只在 UI 層畫成「已就緒」。** 內核 `env_needs` 三態不動。這是權責劃分:
  需要什麼由 tool provider 說清楚,沒說 = 平台告訴使用者它不需要。拿掉 UI 上的 `env.undeclared`
  (「不代表它不需要」)。〔user〕
- 政策是三個 radio,放在每個變數那一列(Vercel 把 Config/Secret 放在變數列上;三個選項不收進下拉——NN/g)。
- 共用變數在每支用到它的 tool 底下都出現,輸入框和 radio **連動**(同一個值、同一個政策),標「與 X 共用」。
- 沒有 tool 宣告的變數 → 「其他變數」一段;「+ 加一個由各人自己填的變數」放這裡(不必在文字框寫
  `KEY=` 空值——空字串是值,在 `shared > private` 底下會蓋掉每個人的 private)。
- `.env` 文字框收成「▸ 用 .env 文字編輯(貼上、匯入、匯出)」。它只存值不存政策,貼進來的新變數政策 = 預設。
  「只有我」分頁沒有文字框。〔user〕
- 換掉之後 Env 不再用 `PopoverItem`;它畫成 checkbox 的問題另開 **#868**,不綁在這裡。

### `/w/` 頁面:iframe 上方的平台列

〔user〕平台固定提供入口,不只靠 bridge(頁面沒放按鈕 = 使用者無法登入也無法登出)。位置查過之後
**否決了右下角浮動鈕**:可信 UI 要在 line of death 上方、不被內容遮住(Eric Lawrence;Chromium
*Security Considerations for Browser UI*),浮動鈕在頁面像素裡、會跟頁面自己的浮動鈕搶位置、
會擋住焦點(WCAG 2.4.11)。

```
┌──────────────────────────────────────────────┐
│ 訂單查詢 · 週報                 🔑 登入 ERP ▾ │ ← 平台畫的,約 32px,不疊在頁面上
├──────────────────────────────────────────────┤
│              (iframe:頁面的像素)            │
└──────────────────────────────────────────────┘
```

- 只在需要時畫:item 沒有任何 `private > shared` / `private only` 的 key、也沒有 `IEnvProvider` → 不畫,
  iframe 佔滿,跟今天一樣。
- 右邊文字:「登入 ERP」「登入 ERP、Jira」「需要登入 3 個系統 ▾」「需要設定 MAP_KEY」「已登入 ▾」。
- 點開 = 「只有我」+「這一頁的排程」(綁定鈕)。
- **帳密絕對不經過頁面**:頁面 CSP 擋網路,但有 `writeFile`,碰得到密碼就能寫進 workspace。
  頁面可以用 bridge 請平台開框(例如 tool 回「未授權」時),框畫在 iframe 外。
- app 內的 WUI pane 已有工具列(它就是 line of death),入口放那裡,不另加列。
- 聊天:缺 `private only` 不中斷那一輪,tool 自己失敗;Env 入口在有 `required` 缺值時提示。

## 〔預設〕實作層決定(可推翻)

- `IEnvProvider` 可選擇回傳 `expires_at`,過期當作沒有值;不回就留到被覆蓋或本人登出。
- private 值明文存放(跟 shared 一樣);**superuser 也不能透過 API 讀別人的 private**。DB 管理者讀得到。
- item 刪除或本人失去存取權時,private 列清掉。
- 綁定表、私人表都是 post-`spec.apply` 註冊、無 auto-CRUD(同 `_SandboxAddress` / `DeployedWui`)。

## Phases

每個 phase 先寫會在未修碼上變紅的測試(`/tdd`)。

| P | 做什麼 | 主要落點 |
|---|---|---|
| P1 | 解析規則:純函式 `resolve(shared, private, policy) -> env`,並在**每一個**今天合併 env 的入口換掉 `{**request_env, **item_env}`。先列入口清單再動手;預設政策下輸出與今天逐位元相同(差分測試) | 新 `api/env_layers.py`;`chat_send.py`、`wui_routes.py:536`、`locator.env_vars_of` 的呼叫者 |
| P2 | item 上的政策欄位 + `write_meta` 閘;確認加欄位是否要 bump `Schema` | `apps/base.py`(`env_vars` 旁) |
| P3 | 私人表:(user, item) → dict;本人讀寫、登出=刪;superuser 讀不到;item 刪除 / 失去存取權清掉 | 新 model + routes |
| P4 | `IRequestEnv.env_for` 的結果寫進 private(最後寫的贏);build 仍不問 | `chat_send._resolve_request_env`、`wui_routes._request_env` |
| P5 | 身分:`wui/run` 用按的人的 private(記帳不動);goal driver 續跑、reclaim 用原作者 | `wui_routes.py:441`、goal driver、`turn_reclaim` |
| P6 | 排程綁定:綁定表(trigger_id → user)、綁定 / 取代路由(`execute`)、內容變更失效、兩種通知;sweep 用綁定人的 private,否則 shared + `env_without_request` | `workflow/user_schedule_sweep.py`、`send_notification` |
| P7 | Env 面板:兩分頁、依 tool 分段的可收合清單、三態標題、政策 radio、「其他變數」、`.env` 收合、private 遮罩;拿掉 UI 上的 `env.undeclared`;Env 按鈕顯示對象 | `EnvVarsModal.tsx`、`lib/envNeeds.ts`、i18n |
| P8 | `/w/` 平台列 + pane 工具列入口 + bridge「開登入框」動詞 + 排程綁定 UI | `pages/WuiPage.tsx`、`renderers/wui/` |
| P9 | 文件:`extending-the-platform.md`(兩層、政策、同 item 可讀 private 的界線、沒附 `env.json` = 平台說不需要)、WUI skill(排程沒綁定時 `private only` 拿不到)、`docs/migrations.md` 一筆、`design-history.md` 索引 | docs |

版面(P7/P8)用真 Chromium 在 1280 與 390 兩個寬度量。
