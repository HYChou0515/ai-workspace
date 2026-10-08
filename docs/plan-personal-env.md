# 我的環境變數：登入一次，所有 item 都用到新的 token

> grill 2026-10-07。每條決定標了來源：**〔user〕** = user 逐題定案；**〔預設〕** = 我自己定、列出來給推翻的
> 實作層決定。沒有標的是推論，後面附理由。
>
> 這份推翻 [`plan-wui-viewer-login.md`](plan-wui-viewer-login.md) 的兩條：「private 的鍵是 (user, item)，
> 不是全站一份」（那份的 Q9）與「private 層同名最後寫的贏」（Q5）。實作的 PR 要在那份的標題下加推翻標記。

## 問題

#869 之後，系統登入（`IEnvProvider`）換到的 token 寫進**那一個 item** 的個人值
（`PrivateEnv(user, item)`，`api/private_env.py`）。所以：

1. 使用者在每個用到這個 token 的 item 都要登入一次。
2. token 過期時，要回到每一個 item 再登入一次；沒回去的 item 繼續用舊 token，失敗。
3. 綁定「用我的身分執行」的排程用的是那個 item 裡存的值，沒人會為了排程回去那個 item 重新登入。

要的是：**token 過期時，在一個地方登入，所有用到它的 item 都拿到新的。**

## 用語〔user，D1〕

| 用語 | 指的是 | 程式碼 | 介面 |
|---|---|---|---|
| **共用值** | item 上的一份，看得到 item 的人都讀得到 | `env_vars`（shared layer） | Env 面板「Shared」分頁（A20） |
| **個人值** | 只屬於某個人的值，是下面三種合起來（= private layer） | | |
| ├ **項目個人值** | 這個人在這個 item 自己填的 | `PrivateEnv` | Env 面板「Private」分頁（A20）；文字說「你在這個 workspace 的值」（A22） |
| ├ **通用個人值**（新） | 這個人在所有 item 都適用的 | `PersonalEnv` | 「我的環境變數」頁；Env 面板「Private(跨workspace)」分頁（A20） |
| └ **自動帶入值** | 部署從 SSO 的 request 算出的 | `PrivateSeam`（`IRequestEnv`） | 「由你的登入狀態自動帶入」 |
| **提供方式** | item 對每個變數名稱的設定 | `env_policy` | Shared／Private first／Private only |
| **系統登入** | 按部署提供的登入按鈕，向外部系統換 token | `IEnvProvider` | 「登入 {名稱}」 |
| **無人在場的執行** | 排程、goal、重跑 | `unattended` | |

## 決定

| # | 決定 | 來源 |
|---|---|---|
| D1 | 用語照上表 | 〔user〕「你建議吧」 |
| D2 | 通用個人值**只**進到提供方式把那個名稱設成 Private first 或 Private only 的 item。Shared 或沒設的 item 拿不到，即使共用值裡沒有那個名稱。不做 per-item 的授權 | 〔user〕「就是 policy 定義即可，不是授權」 |
| D3 | 系統登入一律寫進通用個人值，不再寫進項目個人值。**被 A20 取代**：在哪個分頁登入就寫進哪一層 | 〔user〕 |
| D4 | 同名時項目個人值贏過通用個人值 | 〔user〕 |
| D5 | **不清**：手打或系統登入都不會動到任何 item 裡的值 | 〔user〕「不清，登入也不要清吧」（推翻了我原本「登入時清掉同名項目個人值」的提議） |
| D6 | 個人頁叫「我的環境變數」；任何 item 裡的登入按鈕也寫進通用個人值；SSO 自動帶入值不在範圍。**「任何 item 裡的登入按鈕」那半句被 A20 取代** | 〔user〕 |
| D7 | 無人在場的執行：代理誰不變；組法在項目個人值底下、服務帳號上面多墊通用個人值；接受「綁定之後提供方式被改」會讓不在場的人的值進到那個排程 | 〔user〕 |
| D8 | 「我的環境變數」可以手打值，和系統登入的值同地方、同規則 | 〔user〕 |
| D9 | 提供方式的三個選項顯示為 **Shared／Private first／Private only**（中英文介面都用這三個字），不加說明句 | 〔user〕 |
| D10 | 提供一支一次性清理腳本，由營運方決定何時跑：刪掉每個人在各 item 裡、名稱屬於系統登入產物的項目個人值 | 〔user〕「可以提供 script 讓我去清」 |
| D11 | 無人在場的 workflow run 失敗時，除了 item 擁有者，也通知這次 run 代理的那個人；用現有的 `notify()` | 〔user〕「通知用我們的 send notify 不就好了」 |

D2 的理由：通用個人值的範圍是「所有 item」，所以只在 item 明講要的時候才給。不明講也給的話，你打開的
任何 item 的任何 tool（包括別人上架的第三方 tool）都拿得到。這和 #869 Q9 擋的是同一件事，只是改成由
提供方式決定，而不是由「你有沒有在這個 item 登入」決定。代價：item 擁有者把某個名稱設成 Private 後，
打開頁面的人的通用個人值就會進到這個 item 的 tool，不需要那個人同意。自動帶入值（SSO）今天就是這樣，
所以這是平台已經接受的信任模型。〔user〕

D5 的後果：#869 時期在各 item 系統登入留下的項目個人值，依 D4 會一直蓋過通用個人值，直到它被清掉。
D10 的腳本就是處理這批舊值的方式。

## 機制

### 資料

```
PersonalEnv（新，一個人一列；registered 在 spec.apply 之後，沒有 auto-CRUD 路由）
  user_id: str
  values:  dict[str, str]
  names:   list[str]          名稱的順序（store 會重排 dict 的 key，順序會變成 SANDBOX_USER_ENV_KEYS）
  updated: dict[str, int]     每個名稱最後一次寫入的時間（epoch ms），「我的環境變數」顯示用
```

寫入方式照 `PrivateEnv`：整份取代、刪掉舊 revision。理由相同：不留歷史 token；沒有「先刪後建」中間
沒有列的那一瞬間。

### 一個名稱怎麼解析

`resolve_env` 多收一個參數 `personal`（通用個人值；只在有「替誰跑」的時候有值），三種提供方式各自的順序：

| 提供方式 | 順序（左邊先） |
|---|---|
| Shared（`shared_first`，預設） | 共用值 → 項目個人值（含自動帶入值） → 服務帳號 |
| Private first | 項目個人值（含自動帶入值） → **通用個人值** → 服務帳號 → 共用值 |
| Private only | 項目個人值（含自動帶入值） → **通用個人值** → 服務帳號 |

- Shared 的那一列和今天完全一樣：通用個人值不在裡面（D2）。
- 「服務帳號」是無人在場時部署的 `env_without_request`。今天它被合在 private dict 的最底下；這裡要把它
  拆出來當一個獨立參數，才放得進通用個人值下面（D7）。有人在場時它是空的。
- 項目個人值內部的順序不變：自動帶入值蓋過自己填的（#869 Q5 / D12）。

### 誰的通用個人值：沿用「替誰跑」

| 入口 | 用誰的 | 程式位置（今天組 private 的地方） |
|---|---|---|
| 聊天送出 | 送出的人 | `chat_send.py` → `private_layer` |
| 頁面 `callTool` | 按的人 | `wui_routes.py:557` |
| 頁面按鈕的 `wui/run`、workflow 面板的 Run | 按的人（`RunIdentity`） | `workflow_exec.py:304` → `unattended_layer` |
| 排程 | 綁定的人；沒人綁定 → 沒有人 | 同上 |
| goal 每一輪、換 pod 重跑 | 原作者 | `chat_send.py:476`、`:1070` |
| WUI build | 沒有人：`personal` 為空 | `wui_routes.py:313` |

兩道關卡不改，對通用個人值同樣適用：

- 無人在場時，每次用之前確認那個人**現在**還有這個 item 的權限（`store.may`）。沒有 → 他的通用個人值和
  項目個人值都不給。
- run 的 workflow 檔 digest 對不上 → 身分丟掉，通用個人值也不給（`orchestrator._hold_identity_to`）。

每個 workflow 節點、每一輪都重讀，所以 token 換了之後，還在跑的 run 從下一個節點起就用新的。

### 系統登入寫到哪

- 新路由 `GET/PUT/DELETE /me/env`：只能讀寫自己的那一列，路由不收 user 參數（照 `PrivateEnv` 的「只能
  指名自己」）。〔預設〕
- 新路由 `GET /me/env-providers`、`POST /me/env-providers/{id}`：部署設定的 provider 清單，不綁 item，只要
  登入就能用。換到的值由前端寫進 `/me/env`，和今天 item 裡的流程同一個形狀。〔預設〕
- 既有的 item 裡的登入按鈕（Env 面板、`/w/` 頁面上方的平台列）：換到的值改寫進 `/me/env`，不再寫進那個
  item 的項目個人值（D3、D6）。**被 A20 取代**：Env 面板一層一個分頁，在哪個分頁登入就寫進哪一層；`/w/`
  平台列的按鈕只是打開 Env 面板（預設在 Private 分頁）。

### 通知（D11）

`workflow_exec.notify_failure`（`:674`）今天只通知 item 擁有者。改成：

- 收件人 = {擁有者, `RunIdentity.env_user`}，同一個人只發一次。
- 給代理人的那一封：「以你的身分執行的〔workflow〕失敗了；如果是登入過期，到『我的環境變數』重新登入」，
  連到「我的環境變數」。平台分不出失敗原因，所以不寫「token 過期」。
- 去重：同一個人、同一個 item、同一個 workflow、同一天只發一封（`notify(dedup_key=...)`）。〔預設〕
- 有人在場的失敗不另外通知：錯誤已經在畫面上。

### 清理腳本（D10）

`scripts/clear_item_signin_values.py`〔預設：名稱與參數〕

1. 從部署設定（`server.env_providers`）載入 provider，取每個的 `produces`，聯集成「系統登入產物的名稱」。
   和線上用同一份設定，所以名單是線上的名單，不是手抄的。
2. 走過每一列 `PrivateEnv`，找出值裡屬於那個名單的名稱。
3. 預設 dry-run：印出（user、item、名稱），**不印值**，加上總數。
4. `--apply` 才刪：照 store 的寫入方式（整份取代、刪舊 revision），只拿掉那些名稱，其他名稱不動。
5. 可重複跑；第二次跑不會刪到東西。

不碰 `PrivateSeam`（自動帶入值每次 request 會重寫），不碰共用值。

### 介面

- **「我的環境變數」頁**（`/my-env`〔預設〕）：列出自己的每個通用個人值（名稱、最後更新時間、顯示／隱藏、
  清除）、手打新增（D8）、部署提供的登入按鈕。入口加在平台目的地清單（`hooks/usePlatformDestinations.ts`，
  上方導覽與聊天欄選單共用的那份）裡「我的資源」的下一個。〔預設〕
- **item 的 Env 面板**：「使用中」標示加上來源——「你的（這個 item）」或「你的（所有 item）」，看得出是不是
  被項目個人值蓋掉了。〔預設〕**被 A22 取代**：寫分頁名，「使用中：Private」「使用中：Private(跨workspace)」。
- **提供方式選單**：三個選項的文字換成 Shared／Private first／Private only（D9），中英文都用這三個字。
- **`/w/` 平台列**：登入按鈕寫進「我的環境變數」（**被 A20 取代**，見上）；「需要登入」的判斷把通用個人值算進去（依 D2：只算提供
  方式是 Private 的名稱）。

## 每個窗口誰擋

| 情境 | 會發生什麼 | 誰擋 / 接受的理由 |
|---|---|---|
| 綁定排程之後，擁有者把某名稱改成 Private | 綁定人的通用個人值在他不在場時進到這個排程 | 不擋，D7〔user〕 |
| item 擁有者設 Private、把頁面分享出去 | 打開頁面的人跑 tool 時，tool 拿到他的通用個人值 | 不擋，D2〔user〕；和今天的自動帶入值同一個信任模型 |
| 同 item 有 `execute` 的人讀 `/proc/<pid>/environ` | 別人的 tool 跑的那幾秒，讀得到他的通用個人值 | 不擋；#869「知情不擋」〔user，Q13〕延伸到通用個人值，寫進文件 |
| 兩個分頁同時系統登入同一個名稱 | 最後寫的那份留下 | 整份取代，沒有半寫的狀態 |
| run 跑到一半 token 換了 | 下一個節點起用新的 | 每個節點重讀 |
| 被移出 item 的人 | 他綁的排程下一次觸發就不帶他的任何個人值 | `store.may` |
| #869 時期留在 item 裡的舊 token | 依 D4 蓋過新登入的通用個人值 | D10 腳本；介面上「使用中：Private」看得出來 |

## 不做

- 過期偵測、到期前提醒（`IEnvProvider` 回報 `expires_at`；#869 D9 也沒做）。D11 的失敗通知是替代。
- SSO 自動帶入值改成 per user（今天 per item；無人在場時用的是那個 item 最後一次記下的值）。
- 終端機的 `exec`（`file_routes.py:870`）帶 env：今天 shared 和 private 都不帶，這份也不改。
- 授權（per item 同意讀通用個人值）：D2 否決。

## Phases

| phase | 內容 | 驗收（先紅後綠；每個判準突變各紅一條） |
|---|---|---|
| P1 | 這份 plan + design-history 一行 | `tests/docs/test_docs_index.py` 綠 |
| P2 | `PersonalEnv` model + store + `/me/env` 路由 | 只能讀寫自己的；整份取代不留 revision；`updated` 每個名稱各自記；名稱順序保留 |
| P3 | `resolve_env` 加 `personal`、服務帳號拆成獨立參數；所有入口（上表）帶入「替誰跑」的通用個人值；WUI build 為空 | 三種提供方式各自的順序（Shared 不含通用個人值）；每個入口用對的人（parity：一個共用的組法函式，入口只給「誰」）；無人在場時 `store.may` 失敗 → 不給；digest 對不上 → 不給 |
| P4 | 系統登入寫進通用個人值：`/me/env-providers` 路由；item 裡的登入按鈕改寫 `/me/env` | 登入後 item 的項目個人值沒有被寫；另一個 Private item 下一次執行拿到新值（A20 之後：這只對 Private(跨workspace) 分頁與 `/my-env` 頁的登入成立） |
| P5 | 通知（D11） | 代理人收到、擁有者收到、同一人只一封、同天去重 |
| P6 | 前端：「我的環境變數」頁、Env 面板的來源標示、提供方式選項文字、`/w/` 平台列 | 各頁的 FE 測試；真瀏覽器量 390px 與桌面寬度 |
| P7 | 清理腳本（D10） | dry-run 不刪、不印值；`--apply` 只刪產物名稱；第二次跑零筆；名單取自設定（突變名單來源 → 紅） |
| P8 | 文件：`migrations.md` 一條（行為改變：系統登入改寫到「我的環境變數」；清理腳本何時跑、為什麼、不跑的症狀）、`plan-wui-viewer-login.md` 推翻標記、`configuration.md` | mkdocs `--strict` 綠 |
| P9 | 推、draft PR、review 鏡頭、CI | PR body 含「prod 怎麼驗證」 |


## 實作後與上面不同的地方（2026-10-07，實作時決定）

| # | 計劃寫的 | 實際做的 | 為什麼 |
|---|---|---|---|
| A1 | 清理腳本自己載入部署設定、走過每一列 `PrivateEnv` | 工作在 API 裡做：superuser 才能打的 `POST /api/admin/env/clear-item-sign-ins`；腳本 `scripts/clear_item_sign_ins.py` 只透過 HTTP 呼叫它（和 `scripts/run_migrate.py` 同一個做法）。腳本名字也從 `clear_item_signin_values.py` 改成這個 | 獨立腳本要自己重組 API 的 model registry 與 backend，對 Postgres 不保證對；在 API 裡做，用的就是線上那份 store，名單就是 API **實際載入**的 `server.env_providers`（`app.state.env_providers`），比讀設定檔更貼近線上 |
| A2 | 系統登入的值「由前端寫進 `/me/env`」 | 「我的環境變數」頁、以及 Env 面板「Private(跨workspace)」分頁的登入都是**按下就存**，不等儲存鈕；「Shared」「Private」分頁的登入照舊是填表單（A20） | token 是要給下一次執行用的；登入本身就是刻意的動作，再要一個儲存鈕只會讓「登入了但沒存」的狀態出現。共用值那邊的登入填的是擁有者的共用 token，不屬於 D3 |
| A3 | `resolve_env` 多一個 `personal` 參數 | 個人那一側用 `PersonEnv(own, personal, service)` 一路帶到 `resolve_env`；服務帳號（`env_without_request`）從個人值 dict 裡拆出來成為獨立的 `service` | D7 要求通用個人值排在服務帳號之上；服務帳號原本合在個人值 dict 的最底下，不拆出來就排不進去。parity 測試以舊的合併方式為 oracle，證明個人值為空時結果不變 |
| A4 | （未寫）D2 擋在哪裡 | 只擋在 `resolve_env` 的順序表：Shared 的順序裡沒有通用個人值 | 第一版同時用名稱過濾和順序表擋，突變任一個都被另一個蓋住；留一處，突變才會紅 |
| A5 | `notify_failure(run)` | `notify_failure(run, run_id)`；orchestrator 兩條把 run 結束成 error 的路都傳 run id | `WorkflowRun` 本身不帶 id，而代理人記在以 run id 為鍵的 `RunIdentity` |
| A6 | 「需要登入」的判斷只寫了 `/w/` 平台列 | 聊天的 Env 按鈕（`useEnvMissing`）也算進通用個人值 | 同一個判斷有兩個入口；只改一個，聊天裡會繼續叫已經在「我的環境變數」登入的人去登入 |
| A7 | 入口在「我的資源」的下一個 | 同上，圖示用 Env 鈕的 `tag` | 和 item 裡 Env 鈕同一個圖示，看得出是同一件事 |
| A8 | D3、D6：item 裡的登入一律寫進「我的環境變數」 | **被 A20 取代。** 原本：寫到「這個 item 會讀的地方」：「只有我」分頁裡，這個 item 把那個變數設成 Private first／Private only → 存進「我的環境變數」；設成 Shared（沒設）→ 和以前一樣填進這個 item 的表單、按儲存才存。「我的環境變數」頁的登入不變。 | 第一輪 review（F1）：D2 規定 Shared 的 item 不讀「我的環境變數」，照 D3 寫過去，在 Shared item 登入後 tool 拿不到，畫面卻顯示已登入。要「登入一處、全部更新」，擁有者要把變數設成 Private first／Private only——這寫在 runbook |
| A9 | 清理腳本清掉所有 item 裡的登入產物名稱 | 只清「那個名稱設成 Private first／Private only 的 item」**而且**「那個人在我的環境變數也有同名值」的 | Shared item 裡留著的值就是它在用的，清掉會讓它壞掉；只有 Private 的 item 裡，舊值才會蓋過「我的環境變數」——而且只在那裡真的有一個新值可蓋時（第二輪 review F1：那個人還沒重新登入的話，item 裡的值是他唯一的值） |
| A10 | 「有人在場的失敗不另外通知」 | workflow 面板按 Run 的失敗也通知代理人 | 按下 Run 之後 run 在背景跑，按的人可能已經離開畫面；和排程、頁面按鈕是同一條路（orchestrator 結束成 error），分不出來也不該分 |
| A11 | 給代理人的那一封另外發 | 代理人就是擁有者時不另外發，擁有者原本那一封內文帶上「登入過期就到『我的環境變數』重新登入」；代理人已被移出這個 item（`store.may` 不過）就不發 | 同一人收兩封是噪音；被移出的人收到的連結點進去也看不到 item。去重的「同一天」是 UTC 日 |
| A12 | P8 寫到 `configuration.md` | 沒改 | 這次沒有新設定鍵；`server.env_providers` 沒變 |
| A13 | 腳本只有 `--base-url` | 加 `--header "Name: value"`（可重複） | 前面有 SSO 閘道時，superuser 的身分要靠閘道讀的 header 或 cookie；不帶就只能以 `server.default_user` 執行 |
| A14 | 鍵的順序（`SANDBOX_USER_ENV_KEYS`）照所有個人值 | 只算這個 item 要的那些名稱（Private first／Private only） | 第一輪 review（N1）：Shared 的 item 從鍵的排列看得出我在「我的環境變數」有哪些名稱 |
| A15 | （未寫）同時兩個寫入 | 「我的環境變數」的每個寫入（頁面上的新增／移除／登入、Env 面板的登入）在同一個瀏覽器分頁內排同一條隊，一個做完才做下一個；兩個分頁同時改不同名稱仍可能後寫的蓋掉先寫的（每次寫前重讀已把這個窗口縮到一次往返） | 每個寫入都是「讀整列、寫整列」；同時兩個會讀到同一列，後寫的把先寫的蓋掉（第二輪 review F2） |
| A16 | （未寫）登入時 policy 還沒存 | 依面板上「看到的」policy 分流，包括還沒存的修改。**A20 之後不再分流，這一列不再適用** | 第二輪 review F3：依已存的分流，token 落點和分頁上顯示的「使用中」不一致 |
| A17 | （未寫）讀不到「我的環境變數」 | 頁面顯示讀取失敗，不顯示「還沒有值」 | 第二輪 review F4 |
| A18 | （未寫）`/w/` 平台列與聊天 Env 鈕 | 只有在 item 有 Private first／Private only 的名稱時才去讀「我的環境變數」 | 第二輪 review R1：每一頁的平台列都多等一個請求，而 Shared 的頁面根本用不到 |
| A19 | （未寫）已知不修。**A20 之後不會再發生**：登入落在哪一層由分頁決定，不看 policy | 先在共用值分頁改了某名稱的 policy（還沒存）、再到「只有我」登入、最後放棄 policy 的修改：token 照「看到的」policy 落地，可能留在和最後存下的 policy 不一致的地方（例如 Private first 的 item 裡留一份 item 值） | 第三輪 review F2：要「改 policy、登入、放棄」三步都發生才會遇到；離開時的未存提醒會先問一次。遇到時在那個 item 按「清除我在這個 item 的值」，或跑清理腳本 |
| A20 | D3、D6：item 裡的登入一律寫進「我的環境變數」；介面文字「所有參與者」「只有我（這個 item）」 | Env 面板一層一個分頁，名字用 user 定的：**Shared**（共用值）、**Private**（項目個人值，預設打開）、**Private(跨workspace)**（通用個人值，和「我的環境變數」頁同一份）。**在哪個分頁登入或填值，就寫進哪一層**，不看 policy：Shared、Private 填表單、按儲存才存；Private(跨workspace) 登入立刻存，手打的按這個分頁的儲存才存，只寫改過的名稱。Private(跨workspace) 列出這個 item 的工具要的名稱、以及這個 item 設成 Private first／Private only 的名稱，每列寫這個 item 用不用它（設成 Shared 的不用），附連到 `/my-env` 的連結。英文介面的分頁名是 Shared／Private／Private (all workspaces)〔預設，可推翻〕 | 〔user〕「只有我」登入卻改到所有 item，和分頁的範圍不一致；分頁名稱也沒照說好的用語。一層一個分頁，範圍就是看到的範圍。代價：在 Private 分頁替一個設成 Private first／Private only 的名稱登入，會在這個 item 留一份值，依 D4 蓋過 Private(跨workspace) 的值——那一列的「使用中」會寫出來，「清除我在這個 item 的值」可以拿掉。這種值也是清理腳本會清的（那個人在「我的環境變數」也有同名值時），所以 runbook 要營運方先看 dry run |
| A21 | （未寫）已知不修 | (1) 任一分頁按儲存時，若登入框裡打了帳密還沒送出，面板直接關、不先問（master 上本來就這樣，不是這次造成的）。(2) `/my-env` 頁登入後值存不進去時，登入框裡說一次、app 的寫入失敗通知又說一次——那頁的儲存和新增／移除共用一個 mutation，關掉全域通知會連那些一起關。Env 面板的 Private(跨workspace) 分頁已只說一次 | A20 第二輪 review F3、F1：都要先打了東西又走特定路徑才遇到，修法要動共用的關閉判斷或拆 mutation，比問題本身大 |
| A22 | A20 的面板樣子與文字 | 〔user 2026-10-08 看過 demo〕(1) 分頁改成底線式，用分享視窗的同一個元件 `ShareTabs`（原本是三顆按鈕，選中的那顆和主要動作同一種實心橘色，看起來像「按我」）；(2) Private、Private(跨workspace) 也有 `.env` 文字框，和 Shared 同一個元件；但在這兩個分頁，**值才是本體，文字框是值的編輯器**（框裡打字＝把這一層換成解析結果，拿掉的行＝清除那個值，沒動到的行保留原值——格式放不下的尾端空白、換行不會被改掉；沒在打字時框跟著欄位走，匯入後也是；不保留註解）。第一版照 Shared 讓文字框當唯一一份，review 發現 `.env` 格式會去頭尾空白、放不下換行：欄位打不進空白、舊值一打開就算未存、存檔把含換行的值拆成兩個變數，所以換掉。兩個分頁都是「只記改過的名稱、疊在伺服器的值上」，讀到之前不能打字、不能存，沒有「讀到後合併」那一步；(3) 這個面板與「我的環境變數」頁統一說 workspace（原本混用 item、工作區），D1 的介面欄跟著改；「使用中」直接寫分頁名；Private first 一類的提示改成「覆蓋 {現在在用的那個分頁}」；(4) Private(跨workspace) 每列不再寫「這個 workspace 會用／不會用」：Private 分頁的「使用中」已經說了，而那句一定要提到 policy；「我的環境變數」頁也不提 Private first——讀的人不一定能改 policy；(5) 「清除我在這個 workspace 的值」改成紅色（`data-variant="danger"`）、確定沒有值時不能按（讀不到時仍可按：DELETE 刻意不需要讀取權限）、清除後畫面立刻變空；它和 `/my-env` 的「移除」都先確認，確認鈕寫「清除」「移除」並是紅色；`/my-env` 每列的「移除」本身不是紅色——一列一個紅鈕就是 N 個警告鈕（GOV.UK「use sparingly」）；(6) 已知不修：確認框按 Tab 會離開框、螢幕報讀器只念標題（`Dialog.tsx` 沒有 focus trap、不是 `alertdialog`、沒有 `aria-describedby`）；分頁沒有方向鍵切換——都是 master 上就有的，改共用元件要另外做 | user：「tab 為什麼長這樣」「env 格式的輸入怎麼沒了」「wording 很怪」，逐句定了文字；清除要紅、要查 UI/UX guidance：NN/g〈Confirmation Dialogs〉——救不回來的動作先確認、選項寫結果不寫「是／否」；GOV.UK Design System〈Button: warning button〉——紅色只給嚴重且難回復的動作、加一步確認、不能只靠紅色表達 |
