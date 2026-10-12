# 外網小幫手:使用者電腦上的 Python 小程式代替人去外網查,人只在卡片上點頭

**狀態:** 計劃(尚未動工)。延伸 [plan-outside-lookup.md](plan-outside-lookup.md)(#897 的「請幫我查」卡片),不推翻它:
手動流程原封不動留著當備援。
**來源標記:** 〔user〕= user 的原話或明確選擇(2026-10-12 grill-me);〔查證〕= 讀 `origin/master` 程式碼確認的事實;
〔施工〕= 我定的實作細節,可推翻。

## 1. 問題

「請幫我查」卡片能用,但**人當搬運工太累**〔user〕:每一頁都要選取、複製、切回聊天、貼上,不是模型的問題
(〔user〕「不是智商問題」)。

限制〔user〕:

- 使用者的電腦**只有瀏覽器能上網**,其他程式連不到外面;瀏覽器是 **Chrome**。
- 瀏覽器上不能裝東西(擴充功能、書籤小程式都不行);不能下載 exe。
- 電腦上**有 Python**。
- 登入 app 一次可以維持**以天計**。

已經有的零件〔查證〕:

- `ask_outside` 工具 + 卡片(`plan-outside-lookup.md` §3、§8):查詢模式 / 開網址模式、可改的查詢、目的地按鈕、
  貼上區、附檔、「查不到／不查了」。
- 回覆路由 `POST /a/{slug}/items/{id}/chats/{chat_id}/outside-answers`:存 `lookups/<日期>-<摘要>.md`、附檔、
  送一則帶 `answers` 的使用者訊息;權限(`converse`/`add_content`)、同卡鎖、409 都已處理(§8 A3、A9)。
- 身分〔查證〕:app 本身沒有登入機制;`get_user_id` 由部署注入,讀部署前面 gateway/SSO 發的 cookie(一組 access +
  refresh,refresh 只能用一次)。所以**把使用者瀏覽器的 cookie 複製給別的程式不可行**——誰先換新,另一邊就失效。

## 2. 決定

| # | 決定 | 來源 |
|---|---|---|
| D1 | 要省的是搬運,不是讓 AI 變聰明 | 〔user〕「對 人當搬運工太累」「不是智商問題」 |
| D2 | 使用者電腦上跑一支 Python 小程式(外網小幫手)代替人上外網 | 〔user〕「一個python小工具」 |
| D3 | 因為只有瀏覽器能上網,小幫手用 Playwright 開公司裝好的 **Chrome**(`channel="chrome"`)代為操作;不裝擴充功能 | 〔user〕「只能用瀏覽器上網」「我們是chrome」 |
| D4 | 人在**兩個點**點頭,兩個都在聊天卡片上:① 查詢送出去前(看得到、可以改)按「查」;② 結果回來後勾要哪幾頁,按「送給 AI」 | 〔user〕選 B;「比我想的還好」 |
| D5 | 小幫手主動向後端領工作(網頁不能控制它);**Python 直接連我們的後端** | 〔user〕選 1 |
| D6 | 身分沿用 app 現有的登入(「要登入才能用」那道門),不另做金鑰 | 〔user〕「我們已經有卡(限制)登入了」 |
| D7 | 小幫手**不是必要條件**:沒連線時,卡片就是今天的手動流程 | 〔user〕「小程式不該是dependency」 |
| D8 | 從我們的 app 下載離線安裝包,用電腦上的 Python 安裝(不能下載 exe) | 〔user〕「公司不能下載exe」「有python」 |
| D9 | 做成通用的「外網代理工人」:每種差事是一個瀏覽器操作腳本;第一批是「搜尋」和「開網址」(對應卡片兩種模式) | 〔user〕「他很有未來」;外面只能用瀏覽器碰網路 |

## 3. 機制

### 3.1 流程

```
AI 呼叫 ask_outside ──► 卡片(D7:沒連線 = 今天的手動流程)
                         │ 小幫手已連線
                         ▼
  ① 你改查詢、按「查」 ──► 後端建一筆 OutsideJob(待領)
                                   ▲  │
                 小幫手每幾秒來領 ──┘  ▼
                 Chrome 搜尋 / 開網址、抓正文 ──► 交回結果(每頁:標題、網址、正文)
                                                    │
  ② 卡片列出結果,你勾選、按「送給 AI」 ◄──────────────┘
         │
         ▼
  既有的 outside-answers 路由:存 lookups/ 檔、送帶 answers 的訊息 ──► AI 下一輪讀
```

### 3.2 後端:對外工作(`OutsideJob`)〔施工:名稱、欄位、路由路徑〕

一筆 specstar resource(跨 pod 共用,不放記憶體):`owner`(按「查」的人)、`item_id`、`chat_id`、
`tool_call_id`、`kind`(`search` | `open_url`)、`args`(實際要查的字或網址——①改過的版本)、`state`
(`queued` → `claimed` → `done` | `failed` | `cancelled`)、`claimed_at`、`results`(每頁:`title`、`url`、`text`,
每頁正文有上限〔施工:值〕)、`error`。

- **卡片的路由**(權限同送訊息,`converse`):建立工作(①)、讀工作狀態、取消。
- **小幫手的路由**:報到/心跳、領工作(只領 `owner == 呼叫者` 的,CAS 把 `queued` 改 `claimed`)、交結果、交失敗。
  `claimed` 太久沒交(小幫手關掉、當掉)就回到 `queued`〔施工:逾時值〕。
- **小幫手連線狀態**:每個使用者最後一次心跳的時間;卡片讀它決定畫「交給小幫手」還是手動流程(D7)。
- **送給 AI(②)**:伺服器把勾選的頁組成內容,呼叫既有 outside-answers 的同一段程式(不經瀏覽器再傳一次正文)。
  檔頭多記「由小幫手抓取」與每頁網址。
- 正文只存在 `OutsideJob` 裡直到送出;送出後存成 `lookups/` 檔案,工作標成 `done`。沒送出的工作定期清掉〔施工〕。

### 3.3 身分(D5、D6)〔施工〕

- 小幫手用**自己的** Chrome 設定檔(Playwright persistent context,放在使用者家目錄),第一次啟動時開我們的 app,
  你照平常的方式登入(SSO 怎麼走就怎麼走)。登入後的 cookie 屬於這個設定檔,不碰你平常瀏覽器的 cookie
  (refresh 只能用一次,共用會互相登出)。
- 小幫手連後端用 Playwright 的 `APIRequestContext`(和那個 Chrome 設定檔共用 cookie)——是 Python 直接發 HTTP,
  不經網頁 JS(D5);gateway 換發的 cookie 也自動存回同一個設定檔。
- 後端用 `GET /api/me` 判斷是否已登入;過期(401)時小幫手把 Chrome 視窗叫到前面、開登入頁,等你登入,這段時間
  的工作留在 `queued`,不丟。

### 3.4 小幫手(`outside-helper/`)〔施工〕

- 一支 Python 套件,`python -m outside_helper --app <網址>` 啟動。
- **差事外掛**(D9):每種差事一個模組,宣告 `kind`、參數、執行函式(拿到一個 Playwright page、回傳結果)。
  第一批:
  - `search`:在 Google 搜尋(用卡片目的地清單裡的 Google 那一筆),取前 5 筆結果〔施工:值〕,逐一開頁抓正文。
  - `open_url`:開網址、抓正文。
- **抓正文**:在頁面裡執行內附(vendored)的 Mozilla Readability,取標題與正文;失敗就退回 `document.body.innerText`。
- **視窗開著跑**(headed):比較不會被當成機器人;遇到驗證碼,小幫手把視窗叫到前面、在卡片上顯示「請在小幫手
  視窗裡完成驗證」,等你解完再繼續。
- 一次只做一件差事;做的時候在自己的視窗標題和卡片上顯示「正在查:…」。

### 3.5 卡片(前端)

- 讀小幫手連線狀態(D7):沒連線 → 今天的手動流程,上方多一行「外網小幫手未連線」+ 怎麼安裝的連結。
- 已連線:查詢框照舊可改;主要按鈕變成「查」(交給小幫手);目的地按鈕和貼上區收進「自己查」展開區(手動照樣可用)。
- 進行中:顯示「小幫手正在查…」與取消。
- 結果:每頁一列(標題、網址、正文摘要、可展開全文、勾選框,預設全勾〔施工〕);「送給 AI」(filled)、「重查」、
  「查不到／不查了」。
- 送出後的狀態照 `plan-outside-lookup` §3.4 第 5 點(唯讀、留著當紀錄)。

### 3.6 安裝包(D8)〔施工〕

- CI 產生 wheelhouse(小幫手 + Playwright 的 Windows wheel),和 image 一起出貨;app 有一頁「外網小幫手」提供下載與
  三行安裝說明(`pip install --no-index --find-links <資料夾> outside-helper`)。
- 不下載 Playwright 自帶的瀏覽器(`channel="chrome"` 用已安裝的 Chrome)。

## 4. Phases

| Phase | 內容 | 完成的判準 |
|---|---|---|
| P1 | 這份計劃 | 文件測試、mkdocs `--strict` 綠 |
| P2 | `OutsideJob` resource + 卡片路由(建立/讀/取消)+ 小幫手路由(心跳/領/交結果/交失敗、逾時回收) | 只領自己的工作;CAS 不雙領;逾時回 `queued`;權限同送訊息 |
| P3 | 「送給 AI」:勾選的頁經既有 outside-answers 存檔 + 送 `answers` 訊息 | 與手動送出走同一段程式(parity 測試);檔頭記來源網址 |
| P4 | 卡片:連線狀態、交給小幫手、進行中/取消、結果勾選、送出;沒連線退回手動 | 小幫手沒連線時卡片與今天一模一樣 |
| P5 | `outside-helper/` 骨架:啟動、Chrome persistent context、登入與過期重登、領工作迴圈、差事外掛介面、驗證碼等待 | 對本機假後端 + 本機假網站跑通 |
| P6 | 差事 `search` / `open_url`:Google 結果解析、Readability 抓正文 | 對本機假搜尋頁與真實頁面樣本抓得出標題正文 |
| P7 | 安裝包:CI 產 wheelhouse、app 下載頁 | 乾淨的 Windows Python 只用安裝包就裝得起來 |
| P8 | 文件:`docs/migrations.md`(新 resource、新路由、新下載物)、使用者說明 | mkdocs `--strict` 綠 |
| P9 | live check(真 app + 真 Chrome + 本機假網站)+ demo MP4 | 卡片 ① ② 兩個點都走過,`lookups/` 有檔 |
| P10 | review(四把鏡頭)→ CI | 照 CLAUDE.md |

## 5. 不做(這一版)

- 「搜尋」「開網址」以外的差事(D9 的介面先留著)。
- 一台共用的小幫手(每個人跑自己的;D6 的身分就是登入的那個人)。
- Google 以外目的地的結果解析(其他目的地照舊走手動按鈕)。
- 自動判斷結果有沒有回答問題(由 AI 下一輪讀)。

## 6. prod 怎麼驗證

1. 從 app 的「外網小幫手」頁下載安裝包,照三行說明在自己電腦裝好、啟動;第一次會開 Chrome 請你登入 app。
2. 在聊天問一個需要外部資料的問題;卡片上方顯示小幫手已連線,主要按鈕是「查」。
3. 改一下查詢、按「查」:小幫手的 Chrome 搜尋改過的字;卡片列出幾頁結果。
4. 勾兩頁、按「送給 AI」:`lookups/` 多一個檔,AI 下一輪引用它作答。
5. 關掉小幫手再問一次:卡片回到手動流程(Google 按鈕 + 貼上區)。
