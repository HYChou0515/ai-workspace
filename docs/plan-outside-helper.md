# 外網小幫手:使用者電腦上的 Python 小程式代替人去外網查,AI 自己寫的字出去前人點頭

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
| D4 | **搜尋**送出去前,人在卡片上點頭(看得到查詢、可以改)按「查」。抓回來的內容**都直接送給 AI**,不再經人 | 〔user〕先選 B(兩個點),後改「直接送」——每頁只回一頁(D10),第二個點只剩「按送出」的搬運 |
| D13 | **怎麼開**:雙擊桌面的「外網小幫手」捷徑(由安裝建立,D16),開一個小視窗(Python 內建 tkinter):狀態(已連線/正在查/登入過期)、「登入」「暫停/繼續」「結束」、可勾「開機時自動啟動」(在 Windows「啟動」資料夾放捷徑)。關掉視窗 = 關掉小幫手 | 〔user〕「你如果能安裝好我也是可以」;呼應最初「一個簡單的gui代勞」 |
| D14 | **打包在 dev server**(連得到 PyPI):`pip download --platform win_amd64 --only-binary=:all:` 抓 Windows 用的套件(Playwright 本身不分 Python 版本;`greenlet` 依版本,一次放 CPython 3.10–3.14 各一份〔施工〕),加上小幫手自己的 wheel 和 `.pyw`,組成離線安裝包;放在 **AI workspace 網頁**的「外網小幫手」頁下載。沒有 Windows CI——Windows 上的驗證是 user 在公司筆電裝起來跑一次 | 〔user〕「只能是我在dev server打包 或是在公司筆電打包」「可以 甚至可以在ai workspace網頁下載」 |
| D15 | **更新**:一般更新由小幫手在背景下載、裝好,**下次開啟時生效**,使用者不用做任何事;只有後端說「低於最低可用版本」時,才跳視窗要求立刻更新 | 〔user〕「要放給使用者的東西 要有好的update流程」;選「一般自動、低於最低版本才強制」 |
| D16 | **安裝(每台筆電一次)**:從 AI workspace 網頁下載 zip、解壓縮、雙擊 `安裝.pyw`(只用 Python 內建功能)。它先顯示「將使用 Python 3.x(完整路徑)」讓人確認,建好資料夾、裝好套件,在桌面放捷徑;**捷徑寫死那個 `pythonw.exe` 的絕對路徑**,之後不靠副檔名關聯。系統挑錯 Python 時,取消、改用 `py -3.x 安裝.pyw`。不處理「Python 被升級」——沒有人會自動升級 Python,真的換了就重跑一次安裝 | 〔user〕「什麼叫執行安裝指令?我是在dev server打包的耶」「可以吧 簡單即可」「沒有人會自動升級python」「你有指定執行檔路徑?」 |
| D12 | **開網址**:網址若出現在**這個聊天先前任何一次查回來的內容**裡,直接開;否則和搜尋一樣,先在卡片上點頭 | 〔user〕「純網址可以直接開 搜尋要先確認」,後收緊為「網址出現在上一次查詢中的話就可以直接開」「查過都算」——外面網頁上的連結是外面帶回來的,AI 自己寫的網址才可能夾帶內部字詞 |
| D5 | 小幫手主動向後端領工作(網頁不能控制它);**Python 直接連我們的後端** | 〔user〕選 1 |
| D6 | 身分沿用 app 現有的登入(「要登入才能用」那道門),不另做金鑰 | 〔user〕「我們已經有卡(限制)登入了」 |
| D7 | 小幫手**不是必要條件**:沒連線時,卡片就是今天的手動流程 | 〔user〕「小程式不該是dependency」 |
| D8 | 從我們的 app 下載離線安裝包,用電腦上的 Python 安裝(不能下載 exe) | 〔user〕「公司不能下載exe」「有python」 |
| D9 | 做成通用的「外網代理工人」:每種差事是一個瀏覽器操作腳本;第一批是「搜尋」和「開網址」(對應卡片兩種模式) | 〔user〕「他很有未來」;外面只能用瀏覽器碰網路 |
| D10 | 照人現在的做法:「搜尋」只帶回**搜尋結果頁本身**,由 AI 決定要開哪些連結(再出「開網址」卡片);小幫手不自己挑頁 | 〔user〕「應該全選copy給ai讓他決定要看什麼,就像現在人的做法一樣」 |
| D11 | 抓內容就是「全選、複製、貼到卡片」:整頁 HTML 用**卡片貼上時同一支轉換**(`web/src/lib/htmlToMarkdown.ts`:DOMPurify + turndown + GFM)轉成 Markdown;不去選單、不抽正文,連結與表格留著 | 〔user〕「應該像是全選貼上」「網頁內容轉md應該可以有套件?像我們前端有這種東西」;同 plan-outside-lookup D11「處理越少越好」 |

## 3. 機制

### 3.1 流程

```
AI 呼叫 ask_outside ──► 卡片(D7:沒連線 = 今天的手動流程)
                         │ 小幫手已連線
                         ▼
  搜尋,或沒查過的網址:你看、可以改、按「查」 ─┐
  查過的內容裡出現過的網址:不用按,卡片一出就建 ──┴► 後端建一筆 OutsideJob(待領)
                                          ▲  │
                        小幫手每幾秒來領 ──┘  ▼
                        Chrome 搜尋 / 開網址,整頁轉 Markdown(和貼上同一支,D11)
                                              │ 交回這一頁(標題、網址、文字)
                                              ▼
  既有的 outside-answers 路由:存 lookups/ 檔、送帶 answers 的訊息 ──► AI 下一輪讀
  (想看搜尋結果裡的哪一頁,AI 再出一張「開網址」卡片——D10)
```

### 3.2 後端:對外工作(`OutsideJob`)〔施工:名稱、欄位、路由路徑〕

一筆 specstar resource(跨 pod 共用,不放記憶體):`owner`(按「查」的人)、`item_id`、`chat_id`、
`tool_call_id`、`kind`(`search` | `open_url`)、`args`(實際要查的字或網址——人改過的版本)、`state`
(`queued` → `claimed` → `done` | `failed` | `cancelled`)、`claimed_at`、`error`。抓回來的內容不存在這裡(D4:直接送)。

- **卡片的路由**(權限同送訊息,`converse`):建立工作、讀工作狀態、取消。
- **查過的網址**(D12):每次結果經 outside-answers 存檔時(小幫手抓的、人手動貼的都算——兩者都是外面的內容),
  從內容的 Markdown 連結抽出網址,記在這個聊天名下〔施工:存放處〕。比對時去掉 `#` 之後的錨點,其餘逐字相同〔施工〕。
- **開網址自動建工作**(D12):`ask_outside(url=…)` 畫出卡片時,網址在這個聊天查過的網址裡,伺服器就順手為**送出這一輪的人**
  建好工作;不在裡面就和搜尋一樣等人按「查」。小幫手沒連線就不建,卡片照今天的手動流程(D7)。
- **小幫手的路由**:報到/心跳、領工作(只領 `owner == 呼叫者` 的,CAS 把 `queued` 改 `claimed`)、交結果、交失敗。
  `claimed` 太久沒交(小幫手關掉、當掉)就回到 `queued`〔施工:逾時值〕。
- **小幫手連線狀態**:每個使用者最後一次心跳的時間;卡片讀它決定畫「交給小幫手」還是手動流程(D7)。
- **交結果 = 送給 AI**(D4):小幫手交回這一頁,伺服器以**按「查」的那個人**的身分,呼叫既有 outside-answers 的
  同一段程式(存 `lookups/` 檔 + 送帶 `answers` 的訊息),工作標成 `done`。檔頭多記「由小幫手抓取」與網址。
- **交失敗**(找不到頁面、逾時、被擋):工作標成 `failed`,卡片顯示原因,可以「重查」或改用手動流程。

### 3.3 身分(D5、D6)〔施工〕

- 小幫手用**自己的** Chrome 設定檔(Playwright persistent context,放在使用者家目錄),第一次啟動時開我們的 app,
  你照平常的方式登入(SSO 怎麼走就怎麼走)。登入後的 cookie 屬於這個設定檔,不碰你平常瀏覽器的 cookie
  (refresh 只能用一次,共用會互相登出)。
- 小幫手連後端用 Playwright 的 `APIRequestContext`(和那個 Chrome 設定檔共用 cookie)——是 Python 直接發 HTTP,
  不經網頁 JS(D5);gateway 換發的 cookie 也自動存回同一個設定檔。
- 後端用 `GET /api/me` 判斷是否已登入;過期(401)時小幫手把 Chrome 視窗叫到前面、開登入頁,等你登入,這段時間
  的工作留在 `queued`,不丟。

### 3.4 小幫手(`outside-helper/`)〔施工〕

- 一支 Python 套件;使用者雙擊桌面捷徑開(D13、D16),第一次問 app 網址並記住。
- **差事外掛**(D9):每種差事一個模組,宣告 `kind`、參數、執行函式(拿到一個 Playwright page、回傳結果)。
  第一批:
  - `search`:開搜尋網址(卡片目的地清單裡 Google 那一筆的 `{q}` 代入),帶回**搜尋結果頁本身**(D10)。
  - `open_url`:開網址,帶回那一頁。
- **抓內容 = 全選複製貼上**(D11):前端的 `htmlToMarkdown` 打包成一個獨立的 JS 檔(建置時產生,隨小幫手安裝),
  小幫手開完頁後用 Playwright 在頁面裡執行它,把整頁的 HTML 轉成 Markdown,加上頁面標題與網址帶回。轉換在小幫手的
  Chrome 裡做,不碰網路。連結以 `[文字](網址)` 留在 Markdown 裡,AI 從中挑要開哪一頁(D10),不另附連結清單。
- **兩邊不分岔**:同一組 HTML 範例,前端 `htmlToMarkdown` 與小幫手打包後的轉換必須逐字相同(parity 測試,前端為準);
  打包檔由建置從前端原始碼產生,不手抄。
- **視窗開著跑**(headed):比較不會被當成機器人;遇到驗證碼,小幫手把視窗叫到前面、在卡片上顯示「請在小幫手
  視窗裡完成驗證」,等你解完再繼續。
- 一次只做一件差事;做的時候在自己的視窗標題和卡片上顯示「正在查:…」。

### 3.5 卡片(前端)

- 讀小幫手連線狀態(D7):沒連線 → 今天的手動流程,上方多一行「外網小幫手未連線」+ 怎麼安裝的連結。
- 已連線、搜尋:查詢框照舊可改;主要按鈕變成「查」(交給小幫手);目的地按鈕和貼上區收進「自己查」展開區(手動照樣可用)。
- 已連線、開網址且查過(D12):不出「查」,直接顯示「小幫手正在開…」、完整網址(明文,照 plan-outside-lookup D5)、
  「來自先前查到的內容」和取消。沒查過的網址:和搜尋一樣,網址可看、按「查」。
- 進行中:顯示「小幫手正在查…」與取消。
- 完成:卡片照 `plan-outside-lookup` §3.4 第 5 點收起(唯讀、留著當紀錄),顯示「小幫手已送給 AI」和存檔路徑。
- 失敗:顯示原因,「重查」或展開手動流程。

### 3.6 安裝包(D8)〔施工〕

- dev server 上一支打包指令(D14)產生 wheelhouse(小幫手 + Playwright、greenlet 等 Windows 用的 wheel)與
  `安裝.pyw`,壓成一個 zip;AI workspace 網頁有一頁「外網小幫手」提供下載與一行說明(解壓縮、雙擊 `安裝.pyw`,D16)。
- 不下載 Playwright 自帶的瀏覽器(`channel="chrome"` 用已安裝的 Chrome)。
- **跨平台下載要指定每個 Python 版本**:`pip download` 的 `--python-version`/`--implementation`/`--abi` 預設是
  跑 pip 的那台 Linux 的版本([pip download](https://pip.pypa.io/en/stable/cli/pip_download/));`greenlet` 每個
  CPython 小版本一個 wheel([PyPI](https://pypi.org/project/greenlet/#files))。所以對 3.10–3.14 各跑一次
  `pip download --platform win_amd64 --implementation cp --python-version 3.X --abi cp3X --only-binary=:all:`
  進同一個 wheelhouse;版本用 `==` 釘死,`--no-index` 才不會撿到資料夾裡留下的舊 wheel。

### 3.7 更新(D15)

研究過的坑與對策(來源附在每條;user 要求「去查一下有什麼 pitfall」):

| 坑 | 使用者看到的 | 對策 |
|---|---|---|
| 小幫手與後端版本不一致(我們滾動升級後端) | 舊小幫手問錯路由、看不懂新欄位,默默不工作 | 每個請求帶小幫手版本與協定號;後端回「最低可用版本」,低於它回明確的「請更新」(426 + 下載位置),小幫手跳視窗——不當成網路錯誤。後端 API 只加不刪,欄位要等最低版本越過所有讀它的舊版才拿掉。同 Sparkle 的 critical update / minimum version([Sparkle](https://sparkle-project.org/documentation/publishing/)) |
| 公司 Chrome 自動更新,Playwright 跟不上 | 啟動逾時、Chrome 一開就關 | Playwright 每版只保證當時那一版 Chrome([release notes](https://playwright.dev/python/docs/release-notes));企業政策也可能擋自動化([browsers](https://playwright.dev/python/docs/browsers))。心跳回報 Chrome / Playwright / Python 版本,看得到誰快出事;Chrome 超過測過的版本就出新版小幫手;啟動失敗顯示「Chrome 已更新,請更新小幫手」而不是錯誤堆疊 |
| Windows 不能覆蓋正在跑的檔案 | `pip install --upgrade` 失敗(WinError 32);更新一半壞掉 | **不原地更新**:每個版本裝在自己的資料夾,下次啟動才換,換不成就留舊版——Chrome 與 VS Code 的做法([Chromium upgrade_util_win](https://chromium.googlesource.com/chromium/src/+/lkgr/chrome/browser/first_run/upgrade_util_win.h)) |
| 一台電腦好幾個 Python | 「裝好了,雙擊卻找不到模組」 | `pip`、`py`、`.pyw` 可能各找到不同 Python([Python on Windows](https://docs.python.org/3/using/windows.html));venv 綁死建它的 Python([venv](https://docs.python.org/3/library/venv.html))。所以安裝時讓人確認用哪個 Python,捷徑寫死它的絕對路徑(D16),之後不靠副檔名關聯;每版一個 venv,都由這個 Python 建 |
| 開機捷徑指著舊路徑 | 開機沒啟動,或啟動舊版 | 捷徑永遠指向「安裝時那個 `pythonw.exe` + 同一個固定的啟動檔」,不指向版本資料夾 |
| 公司防毒擋 Playwright 的 `node.exe`;下載的 zip 被標記 | 「Could not connect to the playwright process」;第一次雙擊跳警告 | 企業防毒擋過 Playwright 的執行檔([playwright-java#1016](https://github.com/microsoft/playwright-java/issues/1016));請 IT 依資料夾或簽章放行一次。之後的更新由小幫手從內網直接拿,不經瀏覽器下載,沒有「從網路下載」的標記 |
| 同一個 Chrome 設定檔不能同時開兩個;Chrome 降版 | 開機自動啟動 + 又雙擊一次,第二個失敗;「設定檔來自較新版本」 | 一次只能有一個小幫手([BrowserType](https://playwright.dev/python/docs/api/class-browsertype));設定檔放在版本資料夾外面,更新不會登出;壞掉時有「重設登入」 |

機制〔施工〕:

- **後端說了算**:`GET` 一支版本資訊(最低可用版本、最新版本、協定號、安裝包網址、sha256)。每個請求帶小幫手版本;
  低於最低版本回 426,小幫手跳「請更新」視窗(D15 的強制更新)。
- **版本並排、下次啟動才換**:`%LOCALAPPDATA%\OutsideHelper\versions\<版本>\venv`,外加一個指標檔記錄現在用哪一版
  (用 `os.replace` 原子寫入),上一版留著。小幫手在背景:從後端下載新安裝包 → 驗 sha256 → 建新 venv →
  `pip install --no-index` → 試跑(能 import、`playwright --version`)→ 換指標。新版連續啟動失敗 N 次,啟動檔自動退回
  上一版,並在心跳回報。
- **固定的啟動檔**:桌面捷徑和開機捷徑都是「安裝時那個 `pythonw.exe` + `%LOCALAPPDATA%\OutsideHelper\啟動.pyw`」——
  啟動檔只用標準函式庫,讀指標、用那一版 venv 的 `pythonw.exe` 啟動、確保只開一個。它幾乎不更新,所以捷徑永遠有效。
- **Chrome 設定檔**放在 `%LOCALAPPDATA%\OutsideHelper\profile`,不在版本資料夾裡;單一實例鎖、殘留鎖重試、「重設登入」。
- **第一次安裝**:D16(雙擊 `安裝.pyw`)建好上面的結構與捷徑;之後都由小幫手自己更新。

## 4. Phases

| Phase | 內容 | 完成的判準 |
|---|---|---|
| P1 | 這份計劃 | 文件測試、mkdocs `--strict` 綠 |
| P2 | `OutsideJob` resource + 卡片路由(建立/讀/取消)+ 小幫手路由(心跳/領/交失敗、逾時回收) | 只領自己的工作;CAS 不雙領;逾時回 `queued`;權限同送訊息 |
| P3 | 交結果 = 送給 AI:經既有 outside-answers 存檔 + 以按「查」的人送 `answers` 訊息;存檔時抽出內容裡的連結記為查過的網址,`ask_outside(url=…)` 比對後自動建工作(D12) | 與手動送出走同一段程式(parity 測試);檔頭記來源網址;同一張卡不會送兩次;查過的網址自動開、沒查過的等「查」;手動貼上的也算;錨點不同視為同一網址 |
| P4 | 卡片:連線狀態、交給小幫手、進行中/取消/失敗;沒連線退回手動 | 小幫手沒連線時卡片與今天一模一樣 |
| P5 | `outside-helper/` 骨架:小視窗(D13)、Chrome persistent context、登入與過期重登、領工作迴圈、差事外掛介面、驗證碼等待 | 對本機假後端 + 本機假網站跑通(Linux) |
| P6 | 差事 `search` / `open_url`:開頁、整頁 HTML 經打包的 `htmlToMarkdown` 轉 Markdown | parity:範例 HTML 在前端與小幫手轉出逐字相同;對本機假搜尋頁抓得到結果連結 |
| P7 | 安裝包:dev server 上的打包指令(D14,產 Windows 用的 wheelhouse + `.pyw`)、AI workspace 的下載頁 | 在 Linux 上產出的安裝包含小幫手與 Playwright、greenlet cp310–cp314 的 win_amd64 wheel,版本釘死;`pip install --no-index --dry-run` 對 win_amd64 解得出全部依賴;下載頁下載得到 |
| P8 | 更新(§3.7):版本資訊路由與 426、版本並排安裝、固定啟動檔、試跑後換指標、失敗退回、設定檔放外面、心跳回報版本 | 舊版收到 426 跳更新視窗;更新中途中斷不影響現在這版;新版起不來 N 次自動退回;`安裝.pyw` 建出的捷徑目標是安裝時那個 `pythonw.exe` 的絕對路徑(Linux 上用假路徑測產生的捷徑內容) |
| P9 | 文件:`docs/migrations.md`(新 resource、新路由、新下載物、IT 要放行的項目)、使用者說明 | mkdocs `--strict` 綠 |
| P10 | live check(真 app + 真 Chrome + 本機假網站)+ demo MP4 | 搜尋與開網址各走一次:按「查」之後不再經人,`lookups/` 有檔、AI 讀到;一次更新(換指標、下次開啟生效) |
| P11 | review(四把鏡頭)→ CI | 照 CLAUDE.md |

## 5. 不做(這一版)

- 「搜尋」「開網址」以外的差事(D9 的介面先留著)。
- 一台共用的小幫手(每個人跑自己的;D6 的身分就是登入的那個人)。
- Google 以外目的地的結果解析(其他目的地照舊走手動按鈕)。
- 自動判斷結果有沒有回答問題(由 AI 下一輪讀)。

## 6. 已知的縫

- **不經人的網址只限查過的**(D12):AI 自己寫的網址一律等人點頭;直接開的只有先前查回來的內容裡本來就有的連結。
  內容是人手動貼的時候,裡面的連結也算查過——那是人自己貼進來、看過的東西。

## 7. prod 怎麼驗證

1. 從 app 的「外網小幫手」頁下載 zip、解壓縮、雙擊 `安裝.pyw`:確認顯示的 Python、按「安裝」,桌面出現捷徑。雙擊捷徑:小視窗出現;
   按「登入」開 Chrome 登入 app,視窗顯示「已連線」。這一步是 Windows 上唯一的驗證(D14):裝不裝得起來、`.pyw` 能不能雙擊、
   捷徑開不開得起來、Chrome 開不開得出來、公司代理與 SSO。
2. 在聊天問一個需要外部資料的問題;卡片上方顯示小幫手已連線,主要按鈕是「查」。
3. 改一下查詢、按「查」:小幫手的 Chrome 搜尋改過的字;不用再按別的,`lookups/` 多一個檔(搜尋結果頁),AI 接著讀。
4. AI 想看結果頁上的其中一頁時出「開網址」卡片;不用按,小幫手直接開那一頁送給 AI,AI 引用它作答。
5. 叫 AI 開一個它自己寫的網址(不在先前的結果裡):卡片等你按「查」。
6. 關掉小幫手再問一次:卡片回到手動流程(Google 按鈕 + 貼上區)。
