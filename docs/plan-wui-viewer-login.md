# 看頁面的人用自己的登入:shared / private 兩層環境變數

> 被 #878（plan-personal-env.md）推翻
> 被 #892（plan-env-request-card.md）推翻

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
| WUI build | **沒有人的**:私人層是空的,照政策解析 shared(`private_only` 拿不到) | 〔#788 沿用;照政策:user 2026-10-07,D11〕 |

`wui/run` 今天是 `captured_user=owner`(`wui_routes.py:441`)。「帳記在誰名下」和「用誰的身分」拆開:
記帳不動,身分換成按的人——否則按一下別人頁面的按鈕就用了別人存的 private。

build 只拿 shared:`dist/` 會進永久儲存、組進每個看這頁的人拿到的文件,bundler 會把 env 烤進產出物
(Vite `VITE_*`)。判準:**產出回給一個人 → 可用他的 private;變成共用成品 → 只能 shared。**
shared 也照政策走(和其他入口同一個 `resolve_env`,私人層傳空的):`private_only` 的名字只會以「替誰跑
的那個人」的值出現,build 不替任何人跑,所以拿不到,即使 shared 有值。

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

## 實作後與上面不同的地方(2026-10-01,實作時決定)

每一條都寫了**為什麼**與**證據**;推翻任何一條只要改回對應的 commit。

**user 確認(2026-10-01):** D1–D3 同意。D5:面板的「執行」用按的人的值,哪一層照 policy(與 chat 同一個
`resolve_env`)。D13:符合「有 `write_meta` 才能設定共用值、沒有的只能設定私人值」,存共用值的權限沒放寬,只是
換 token 的那條 API 降到 `read_meta`。D15:照現在的做法——被移出的人的值留在 DB 但不再被使用。
**user 決定(2026-10-07):** D11 改成 build 也照政策,私人層為空——`private_only` 的名字建頁面時拿不到。

**沒有逐條確認過的:** D4、D7、D8、D12、D14、D16 只確認了「有實作、沒略過」,做法本身沒有討論;D6 解釋過、
user 沒有回覆;D9、D10 沒有拿出來討論過。

| # | 計畫寫的 | 實作做的 | 為什麼 |
|---|---|---|---|
| D1 | 沒有 `env.json` 的 tool 在 UI 上畫成「已就緒」 | **不列出**(沒有區段);任何沒有宣告變數的 tool 都不列 | 真 Chromium 量到:Env 面板把每一支內建工具(Exec、Read File…數十支)都列成「已就緒」,把使用者要填的變數擠出畫面。「沒寫 = 都不需要」照樣成立,只是「不需要」的 tool 沒有東西可列。內核三態不動 |
| D2 | 🔑 按鈕:缺東西時寫要登入什麼,否則「已登入」 | 否則分兩種:本人**有**自己的值 →「已登入」;**沒有** →「你的登入」 | 真瀏覽器看到「Signed in」掛在一個空面板上——沒有「缺」只是因為沒有 tool 把那些名字標必填,「已登入」這句話是假的 |
| D3 | 「只有我」和「這一頁的排程」在同一個下拉 | 平台列上兩顆鈕:🔑 開 Env 面板(「只有我」分頁),「這一頁的排程」開自己的面板 | 重用已測過的 Env 面板,而不是在下拉裡再做一份;排程面板用自己的定位,因為共用的 `Popover` 在 390px 會跑出畫面左邊(#868 在重做它) |
| D4 | Env 按鈕給有 `converse` 或 `execute` 的人 | 給 `converse` 或 `write_meta` 的人 | FE 的 `useItemAccess` 沒有 `execute`;Collaborator 以上都有 `converse`。只被單獨勾了 `execute` 的 Custom 權限看不到這顆鈕;若他也沒有 `read_content`,WUI 頁面本身就打不開,也不會有平台列(review round 1 V8 更正:原本這格寫「他仍有平台列」是錯的) |
| D5 | 頁面按鈕起的 `wui/run` 用按的人的 private | 同上,**加上 `POST …/run`(workflow 面板的 Run)也用按的人** | 同一條規則「有人按的 run 用按的人」;只做一條會讓同一個人、同一個 workflow,從兩扇門進來拿到不同身分 |
| D6 | goal 續跑、重跑用原作者的 private | 用 `{**env_without_request, **原作者的 private}` | 只用 private 會讓已經靠 #809 service account 的部署,在這兩條路上**少掉**那個值(回歸) |
| D7 | (未寫) | 新增 `GET …/env/layers`(`read_meta`)回 item 的 shared 值與政策 | `/w/` 頁面只有 item id、沒有 item 記錄;這兩個欄位 `read_meta` 本來就回得到 |
| D9 | 〔預設〕`IEnvProvider` 可選擇回傳 `expires_at` | **沒做** | 目前沒有任何 provider 回過期時間;過期的 token 會在 tool 那端失敗,重新登入就好。有需要時再加(介面是加欄位,不破壞既有 impl) |
| D10 | 平台列:有 private 政策或有 `IEnvProvider` 才畫 | 另外**頁面有排程**也畫;`IEnvProvider` 只算「產出的名字有 tool 宣告」的 | 「用我的身分執行」的鈕要有地方放;一個跟這頁 tool 無關的 provider 沒有東西可登入 |
| D11 | WUI build 只拿 shared | **照政策,私人層為空**:和其他入口同一個 `resolve_env(shared, {}, policy)`;`shared_first` / `private_first` 拿到共用值,`private_only` 拿不到(即使 shared 有值) | 第一版是「不看政策」,理由是共用值本來大家讀得到;user(2026-10-07)決定 build 也照政策——`private_only` 的意思是「只用那個人自己的」,在任何入口都該成立。測試 `test_the_build_follows_the_items_policy_with_nobody_as_the_private_layer`,突變 `wui_routes` 那一行回 `dict(layers.shared)` 只有它變紅 |
| D12 | `env_for` 的值寫進 private,最後寫的贏 | 存在**另一列**(`PrivateSeam`),每次**整份取代**;同名時它贏過手 key 的;沒人在場的 turn 用 `{**服務帳號, **手 key, **seam}` | review round 1 R2/R3/R4/F8:合在一列時,seam 不再回的名字(登出 SSO)會一直留著、tool 看到的名字順序從第二次起就變、每個輪換過的 token 都留成 revision、seam 寫入和「清除我的值」賽跑會把剛清掉的值寫回來。拆開後四個都不成立;「自動的贏過手 key」這條 user 的決定不變。部署拿掉 seam 後,留下的 seam 列不再被讀(round 2)。寫入是**取代後刪舊 revision**(round 2 D4/D5:先刪再建有一瞬間沒有列;round 3:刪舊 revision 前本人剛好登出,列已不在,不再往外丟錯),名字順序另存一欄照原樣還原(R3)。**「最新」只到本人上一次在這個 item 聊天或按頁面工具為止**:只有這兩條路問 `env_for`,登出 SSO 不是這個 app 看得到的請求,所以之前替他跑的背景工作仍用舊值(round 2 veracity V2) |
| D13 | (#750:`IEnvProvider` 要 `write_meta`) | 改成 `read_meta` | review round 1 C1:看頁面的人要能登入進自己的 private 層,這是這份計畫的主要目的;換出的值只進本人的 private,存成共用值仍要 `write_meta` |
| D14 | run 用按的人的 private | 「按的人」存在 `RunIdentity`(沒有 API 路由),不是 `WorkflowRun` 的欄位 | review round 1 R5:`WorkflowRun` 的 auto-CRUD 沒有寫入閘,放在它上面等於讓任何人 PATCH 一個暫停中的 run 去用別人的值。這只保護了「替誰跑」;`workflow_id` 仍改得到,所以 `RunIdentity` 另記 run 開始時的 `workflow_id` 與 workflow **檔**的 digest(和排程綁定同一個函式),**每次組出要跑的 workflow 時**(`_execute`:開始、gate 決定、續跑、steer)拿「這次讀進來組 interpreter 的那份位元組」的 digest、`workflow_id`、item 與 profile 比,對不上就丟掉身分(round 5:gate 決定走別的 item 的網址時會在那個 item 組,既有的缺口,身分不能跟過去);三處 digest 都由同一個 loader 定義(round 5:壞檔在綁定時算位元組、組的時候算 "");round 6:gate 決定 / steer / steer 確認只接受 run 自己 item 的網址(404;同一類的取消、讀 run、看串流三條沒改,見 #870;身分比對 item 仍留著當第二道),「用我的身分執行」拒絕解析不了的 workflow(422,否則同意記成 "",檔案刪掉後就讓同名 profile workflow 帶著值跑),loader 只認資料夾裡平的 `<id>.json`(和 `unparsable_workflow` 同樣的規則,目前是手抄的兩份,見 #870);組的時候讀檔失敗,run 不會被標成錯誤——新開的停在 pending,gate 決定 / steer 確認之後的停在 running(之後才可能被清理程式標成錯誤);這個 PR 之前就是如此,不會走到身分判斷;已知不修:gate 決定 / 續跑時 manifest 是另一次讀取(輸入預設值、`config` 取自它),digest 只涵蓋組 interpreter 的那份——DSL interpreter 用的是檔案自己的 `config`,換的窗口也只在同一個請求內(round 5 veracity);steer 核准也丟(round 2 D1、veracity V5;round 3:第一版比的是 manifest 的 digest,manifest 不含 steps;round 4 defect 1:改成每個節點讀即時檔案後,「換成惡意檔 → 決定 gate → 換回來」照樣過,因為節點看的是檔案不是正在跑的 interpreter) |
| D15 | 〔預設〕本人失去存取權時 private 列清掉 | **不清列,改成每次「沒有人在場」的使用前確認他還能用**(goal / 重跑 `converse`、run `execute`、排程綁定 `execute`,失去就取消綁定並通知) | 有人在場的路徑本來就先過存取閘;列留著對他無害(只有他讀得到;要刪只能直接打 `DELETE …/env/private`,UI 在他打不開的 Env 面板裡——round 2 veracity V6 更正),真正的洞是背景路徑繼續替他用值——round 1 C2/F1 |
| D8 | 排程內容一改,綁定失效 | 另外:排程列被刪、**排程檔整份被刪**、它要跑的 **workflow 檔內容被改**,都失效並通知 | 前兩者對 sweep 無法區分(key 不在檔案裡);workflow 檔改了而 key 不變,是 review round 1 F4 找到的繞道。判定前以**即時檔案**再確認一次(持久快照會落後),**檔案整份解析不了時什麼都不動**(round 1 F2/F3 更正:第一版在解析失敗時會把整份檔案的綁定全刪)。workflow 檔只比對**即時檔案**(round 2 D3:快照相同時直接放行,會讓快照追上前的修改帶著綁定者的值跑),讀取有和刪除確認同一個逾時上限、逾時當沒變(round 3 regression 1:沒上限時一次卡住的讀取會拖住所有 item 的排程);觸發時把綁定的 digest 交給 run 記下,不在 start 重讀(round 3)。**只比對那一個檔**:它呼叫的腳本、agent 讀的其他檔被改不會取消(veracity V1,已知不修:要把 workflow 會碰到的所有檔都算進同意範圍,沒有可靠的邊界) |
| D16 | run 用按的人的 private,使用前確認他還能 `execute` | workflow 面板的 `POST …/run` 記的權限是 **`converse`**(那扇門本身就只要 `converse`);頁面的 `wui/run` 記 `execute` | round 2 D2:一律確認 `execute` 會讓只有 `converse` 的人按下的 run 被接受、卻默默不帶他的值 |
