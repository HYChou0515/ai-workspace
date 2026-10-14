# 小幫手:使用者電腦上的 Python 小程式代替人去外網查,AI 自己寫的字出去前人點頭

**狀態:** 已施工(PR #927,P2–P10)。延伸 [plan-outside-lookup.md](plan-outside-lookup.md)(#897 的「請幫我查」卡片),不推翻它:
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
| D2 | 使用者電腦上跑一支 Python 小程式(小幫手)代替人上外網 | 〔user〕「一個python小工具」 |
| D3 | 因為只有瀏覽器能上網,小幫手用 Playwright 開公司裝好的 **Chrome**(`channel="chrome"`)代為操作;不裝擴充功能 | 〔user〕「只能用瀏覽器上網」「我們是chrome」 |
| D4 | **搜尋**送出去前,人在卡片上點頭(看得到查詢、可以改)按「查」。抓回來的內容**都直接送給 AI**,不再經人 | 〔user〕先選 B(兩個點),後改「直接送」——每頁只回一頁(D10),第二個點只剩「按送出」的搬運 |
| D13 | **怎麼開**:雙擊桌面捷徑 `AI Workspace Helper`(由安裝建立,D16;檔名用英文),開一個小視窗(Python 內建 tkinter),三個分頁:**狀態**(已連線/正在搜尋（Google）/正在開啟/請登入/登入已過期)與「登入」「暫停/繼續」「結束」、可勾「開機時自動啟動」(在 Windows「啟動」資料夾放捷徑);**查詢紀錄**(最近 200 筆,只存本機 `history.jsonl`);**記錄檔**(`helper.log` 尾端、開啟記錄資料夾)。關掉視窗 = 關掉小幫手 | 〔user〕「你如果能安裝好我也是可以」;呼應最初「一個簡單的gui代勞」;2026-10-13 跑過真視窗後〔user〕「Wording有點醜」「在那邊應該要有搜尋紀錄和log」 |
| D14 | **打包在 dev server**(連得到 PyPI):`pip download --platform win_amd64 --only-binary=:all:` 抓 Windows 用的套件(Playwright 本身不分 Python 版本;`greenlet` 依版本,一次放 CPython 3.10–3.14 各一份〔施工〕),加上小幫手自己的 wheel 和 `.pyw`,組成離線安裝包;放在 **AI Workspace 網頁**的「小幫手」頁下載。沒有 Windows CI——Windows 上的驗證是 user 在公司筆電裝起來跑一次 | 〔user〕「只能是我在dev server打包 或是在公司筆電打包」「可以 甚至可以在ai workspace網頁下載」 |
| D15 | **〔被 D21 取代〕更新**:一般更新由小幫手在背景下載、裝好,**下次開啟時生效**,使用者不用做任何事;只有後端說「低於最低可用版本」時,才跳視窗要求立刻更新 | 〔user〕「要放給使用者的東西 要有好的update流程」;選「一般自動、低於最低版本才強制」 |
| D16 | **安裝(每台筆電一次)**:從 AI Workspace 網頁下載 zip、解壓縮、雙擊 `install.pyw`(只用 Python 內建功能;檔名一律英文)。它先顯示「將使用 Python 3.x(完整路徑)」讓人確認,建好資料夾、裝好套件,在桌面放捷徑;**捷徑寫死那個 `pythonw.exe` 的絕對路徑**,之後不靠副檔名關聯。系統挑錯 Python 時,取消、改用 `py -3.x install.pyw`。不處理「Python 被升級」——沒有人會自動升級 Python,真的換了就重跑一次安裝 | 〔user〕「什麼叫執行安裝指令?我是在dev server打包的耶」「可以吧 簡單即可」「沒有人會自動升級python」「你有指定執行檔路徑?」 |
| D17 | **解除安裝**:小視窗裡的「解除安裝」按鈕,確認後刪掉小幫手留下的三樣東西(`%LOCALAPPDATA%\AIWorkspaceHelper\` 整個資料夾、桌面與啟動資料夾的 `AI Workspace Helper.lnk`);按鈕不能用時,手動刪掉這三樣就等於解除安裝。不碰登錄檔、不裝系統資料夾,所以沒有別的痕跡;後端不用做任何事(沒有心跳,卡片自動退回手動,D7) | 〔user〕「怎麼解安裝」「要」 |
| D18 | **名稱**:app 叫「AI Workspace」;這支程式的正式名稱是「AI Workspace Helper」——桌面捷徑、視窗與對話框標題、安裝資料夾 `%LOCALAPPDATA%\AIWorkspaceHelper\`、下載的 `ai-workspace-helper-<版本>.zip`;視窗裡和網頁上自稱「小幫手」(例如「小幫手已連線」),不再叫「外網小幫手」。程式碼裡叫 `ai-workspace-helper` / `ai_workspace_helper`:`ai-workspace-helper/` 資料夾(Python 套件 `ai_workspace_helper`)、`/api/ai-workspace-helper/*` 路由、`X-AI-Workspace-Helper-*` header、`server.ai_workspace_helper_dir`、`ai-workspace-helper.json`。卡片的工作(`OutsideJob`、`/outside-jobs`)屬於卡片,不改名;本計劃檔名不改 | 〔user〕「名稱改叫ai workspace helper」「中文名也要改啊」「正式名稱AI workspace」(我先讀成程式的名字,其實是 app 的)「視窗內自稱小幫手」「outside-helper.json也改掉」「AI工作區 改為AI Workspace」「AI Workspace.lnk 不是應該叫做ooxx helper?」 |
| D19 | **不需要系統管理員權限,但要被允許執行**:安裝只寫 `%LOCALAPPDATA%\AIWorkspaceHelper\` 與自己的捷徑(不跑安裝程式、不寫 Program Files、不改登錄檔);公司電腦可能擋的是**執行**——使用者資料夾裡的程式(AppLocker)、zip 裡的 `node.exe`(防毒/代理)、PowerShell 受限(只影響捷徑)、Chrome 自動化政策。zip 裡附 **`precheck.pyw`**:不安裝任何東西,在暫存資料夾把每一樣真的試一次、列表、可複製給 IT,結束就刪掉。捷徑建不起來不再算安裝失敗(安裝結束時說要雙擊哪個檔);捷徑的 PowerShell 遇到任何錯誤都算失敗(`$ErrorActionPreference = 'Stop'`,否則沒建出來也會回 0) | 〔user〕「請問他會安裝嗎?我想我們沒有安裝權限」「可以 你就一起寫到docs裡面」 |
| D20 | **小幫手對 AI Workspace 的請求不驗憑證**:那些請求(報到、領工作、交結果、下載更新)由 Playwright 自帶的 Node 送出,不是 Chrome——Node 不讀 Windows 憑證庫、不補伺服器沒送的中繼憑證,於是 Chrome 登入得了的部署,小幫手每次報到都是「unable to verify the first certificate」→「連不上」、卡片「小幫手未連線」(user 的公司筆電,0.1.0)。依這個部署的慣例(user:「我們都是verify=false」)不驗,沒有開關,但**只對這些請求**(`APIRequestContext.fetch/get` 的 `ignore_https_errors`),差事開的網頁仍由 Chrome 驗。代價:更新檔的 sha256 是跟同一條不驗憑證的連線拿到的版本資訊比對,只擋下載損壞、擋不了攔截者換掉更新檔——能在公司網路上攔截這條連線的人,可以讓小幫手裝上並執行他的程式,也看得到登入 cookie(已告知 user)。Playwright 的錯誤訊息附的「Call log」列出每個 header(含登入 cookie),只留第一行再交給視窗與記錄檔。連不上/被拒的原因改為開始時寫進 `helper.log`(原本只在視窗第二行)。另:zip 裡那份 `launch.pyw` 在解壓縮資料夾被雙擊時,改去找已安裝的資料夾(原本說「沒有安裝」);兩邊都沒裝時留在自己的資料夾,不在 `%LOCALAPPDATA%` 留下東西 | 〔user〕「看到 unable to verify the first certificate」「我們都是verify=false」「發現安裝後有開起來 但是按launch仍然說沒有安裝」 |
| D21 | **更新只通知、不自動裝**(取代 D15):小幫手啟動後第一次連上 AI Workspace 時(以及被停用後恢復連線時)、之後每 6 小時讀版本資訊:有新版時小視窗顯示「有新版 X」,被要求更新(426)時顯示「這個版本已停用」,兩者都附「開啟下載頁」按鈕;使用者在**一般瀏覽器**(會驗憑證)下載 zip、重新雙擊 `install.pyw`,它把正在跑的小幫手關掉、換成新版。小幫手不再自己下載、安裝、換版、退回。為什麼:D20 讓小幫手對 AI Workspace 不驗憑證,自動更新走同一條連線,能攔截它的人就能讓小幫手裝上並執行他的程式(sha256 也是從那條連線來的,擋不住);改成只通知後,偽造的通知最多把人帶去下載頁或讓小幫手停止接工作,下載本身由瀏覽器驗 | 〔user〕(兩案:A 照常自動、文件寫明風險;B 只提示、自己從網頁下載重跑 `install.pyw`)「B」;「我覺得先討論完 開發玩 再繼續ci」「寫plan」 |
| D22 | **等跳轉停下來才讀頁**:差事開頁後,等頁面載入完成且網址連續 1.5 秒(`QUIET_MS`)沒變才讀——腳本跳轉、自己送出的表單(SSO 的 form_post)、伺服器轉址,每跳一次重新計時;最多 30 秒(`SETTLE_LIMIT_MS`)。讀到的頁面沒有任何文字或連結/圖片時再等、再讀,到時限還是空的就以中文說明失敗(「這一頁轉出來沒有文字(可能還在跳轉、是 PDF、或要登入)」),不把空頁交給後端。驗證碼頁是終點不是中途站:等待中一看到就停,交給 §3.4 的等人;人解完後重新等一次。跳轉途中去讀頁面會等到新頁出現再以「context destroyed」失敗(實測 Chromium,表單與腳本跳轉都是),所以不另外追蹤進行中的請求,讀失敗就重等重讀 | 〔user〕「現在看到 小幫手沒查到:nothing to send, paste or attach」「我自己打網址是ok的 但我自己打的話中間可能會好幾次跳轉 有影響嗎」「我不知道跳轉邏輯」 |
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

- **卡片的路由**:建立工作、取消(權限同送訊息,`converse`);讀工作狀態用 `read_chat`〔施工〕——只能讀這個聊天的人
  看得到卡片進度,不能代人按「查」。
- **查過的網址**(D12):每次結果經 outside-answers 存檔時(小幫手抓的、人手動貼的都算——兩者都是外面的內容),
  從內容的 Markdown 連結抽出網址,記在這個聊天名下〔施工:存放處〕。比對時去掉 `#` 之後的錨點,其餘逐字相同〔施工〕。
- **開網址自動建工作**(D12):`ask_outside(url=…)` 畫出卡片時,網址在這個聊天查過的網址裡,就為**送出這一輪的人**建好工作;
  不在裡面就和搜尋一樣等人按「查」。小幫手沒連線就不建,卡片照今天的手動流程(D7)。
  〔施工〕由卡片畫出時自己請求(`auto`),伺服器判斷:網址查過、而且請求的人就是這一輪的發問者(最後一則使用者訊息的
  作者)才建——共用 item 裡其他開著聊天的人,他們的小幫手不會以他們的身分去開。代價:發問者沒開著這個聊天時,要等他
  回來卡片才請求;按完「查」的人通常就在畫面上。答案帶回的連結在答案訊息送出**之前**記下(那則訊息會開始下一輪,
  下一輪可能馬上要開其中一個;live check 抓到過先送後記的競態)。
- **小幫手的路由**:報到/心跳、領工作(只領 `owner == 呼叫者` 的,CAS 把 `queued` 改 `claimed`)、交結果、交失敗。
  `claimed` 太久沒交(小幫手關掉、當掉)就回到 `queued`〔施工:逾時值〕;同一份工作被領了 3 次都沒交成(工作區滿了、
  一直被拒、小幫手在那一頁當掉)就標成 `failed` 並寫原因,不再每兩分鐘重抓一次〔施工〕。
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
- 〔施工〕小幫手每次報到的回應判斷是否已登入:401 或被 gateway 導去 SSO(3xx,不跟隨)= 要重新登入;403 是「沒權限」,
  不是登入過期。要重新登入時小幫手把 Chrome 視窗叫到前面、開登入頁,等你登入,這段時間
  的工作留在 `queued`,不丟。

### 3.4 小幫手(`ai-workspace-helper/`)〔施工〕

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
- 開頁後等跳轉停下來才讀(D22)。
- 一次只做一件差事;做的時候在自己的視窗(狀態與標題)顯示「正在搜尋（Google）：…」或「正在開啟：…」,卡片上顯示「小幫手正在查…」。

### 3.5 卡片(前端)

- 讀小幫手連線狀態(D7):沒連線 → 今天的手動流程,上方多一行「小幫手未連線」+ 怎麼安裝的連結。
- 已連線、搜尋:查詢框照舊可改;主要按鈕變成「查」(交給小幫手);目的地按鈕和貼上區收進「自己查」展開區(手動照樣可用)。
- 已連線、開網址且查過(D12):不出「查」,直接顯示「小幫手正在開…」、完整網址(明文,照 plan-outside-lookup D5)、
  「來自先前查到的內容」和取消。沒查過的網址:和搜尋一樣,網址可看、按「查」。
- 進行中:顯示「小幫手正在查…」與取消。
- 完成:卡片照 `plan-outside-lookup` §3.4 第 5 點收起(唯讀、留著當紀錄)。存檔路徑寫在小幫手送出的那則訊息裡(「存在
  `lookups/…`」),就在卡片下面〔施工〕——卡片在那則訊息出現的同時收起,不另外再顯示一次。
- 失敗:顯示原因,「重查」或展開手動流程。

### 3.6 安裝包(D8)〔施工〕

- dev server 上一支打包指令(D14)產生 wheelhouse(小幫手 + Playwright、greenlet 等 Windows 用的 wheel)與
  `install.pyw`、`launch.pyw`,壓成 `ai-workspace-helper-<版本>.zip`;AI Workspace 網頁有一頁「小幫手」提供下載與一行說明(解壓縮、雙擊 `install.pyw`,D16)。發布流程見 §3.7.2。
- 不下載 Playwright 自帶的瀏覽器(`channel="chrome"` 用已安裝的 Chrome)。
- **跨平台下載要指定每個 Python 版本**:`pip download` 的 `--python-version`/`--implementation`/`--abi` 預設是
  跑 pip 的那台 Linux 的版本([pip download](https://pip.pypa.io/en/stable/cli/pip_download/));`greenlet` 每個
  CPython 小版本一個 wheel([PyPI](https://pypi.org/project/greenlet/#files))。所以對 3.10–3.14 各跑一次
  `pip download --platform win_amd64 --implementation cp --python-version 3.X --abi cp3X --only-binary=:all:`
  進同一個 wheelhouse;版本用 `==` 釘死,`--no-index` 才不會撿到資料夾裡留下的舊 wheel。

### 3.7 更新(D15 → D21)

研究過的坑與對策(來源附在每條;user 要求「去查一下有什麼 pitfall」):

| 坑 | 使用者看到的 | 對策 |
|---|---|---|
| 小幫手與後端版本不一致(我們滾動升級後端) | 舊小幫手問錯路由、看不懂新欄位,默默不工作 | 每個請求帶小幫手版本與協定號;後端回「最低可用版本」,低於它回明確的「請更新」(426 + 下載位置),小視窗顯示「這個版本已停用」(D21)——不當成網路錯誤。後端 API 只加不刪,欄位要等最低版本越過所有讀它的舊版才拿掉。同 Sparkle 的 critical update / minimum version([Sparkle](https://sparkle-project.org/documentation/publishing/)) |
| 公司 Chrome 自動更新,Playwright 跟不上 | 啟動逾時、Chrome 一開就關 | Playwright 每版只保證當時那一版 Chrome([release notes](https://playwright.dev/python/docs/release-notes));企業政策也可能擋自動化([browsers](https://playwright.dev/python/docs/browsers))。心跳回報 Chrome / Playwright / Python 版本,看得到誰快出事;Chrome 超過測過的版本就出新版小幫手;啟動失敗顯示「Chrome 已更新,請更新小幫手」而不是錯誤堆疊 |
| Windows 不能覆蓋正在跑的檔案 | `pip install --upgrade` 失敗(WinError 32);更新一半壞掉 | **不原地更新**:每個版本裝在自己的資料夾,下次啟動才換,換不成就留舊版——Chrome 與 VS Code 的做法([Chromium upgrade_util_win](https://chromium.googlesource.com/chromium/src/+/lkgr/chrome/browser/first_run/upgrade_util_win.h)) |
| 一台電腦好幾個 Python | 「裝好了,雙擊卻找不到模組」 | `pip`、`py`、`.pyw` 可能各找到不同 Python([Python on Windows](https://docs.python.org/3/using/windows.html));venv 綁死建它的 Python([venv](https://docs.python.org/3/library/venv.html))。所以安裝時讓人確認用哪個 Python,捷徑寫死它的絕對路徑(D16),之後不靠副檔名關聯;每版一個 venv,都由這個 Python 建 |
| 開機捷徑指著舊路徑 | 開機沒啟動,或啟動舊版 | 捷徑永遠指向「安裝時那個 `pythonw.exe` + 同一個固定的 `launch.pyw`」,不指向版本資料夾 |
| 公司防毒擋 Playwright 的 `node.exe`;下載的 zip 被標記 | 「Could not connect to the playwright process」;第一次雙擊跳警告 | 企業防毒擋過 Playwright 的執行檔([playwright-java#1016](https://github.com/microsoft/playwright-java/issues/1016));請 IT 依資料夾或簽章放行一次。〔D21〕更新也是從瀏覽器下載 zip,和第一次安裝一樣會帶「從網路下載」的標記 |
| 同一個 Chrome 設定檔不能同時開兩個;Chrome 降版 | 開機自動啟動 + 又雙擊一次,第二個失敗;「設定檔來自較新版本」 | 一次只能有一個小幫手([BrowserType](https://playwright.dev/python/docs/api/class-browsertype));設定檔放在版本資料夾外面,更新不會登出;壞掉時有「重設登入」 |

機制〔施工〕(檔名一律英文——〔user〕「檔名都不要用中文」):

#### 3.7.1 使用者電腦上的檔案

```
%LOCALAPPDATA%\AIWorkspaceHelper\
  launch.pyw              固定的啟動檔(只用標準函式庫);捷徑都指向它
  state.json              現在用哪一版、上一版、安裝時的 Python 路徑、開機自動啟動的勾
  helper.lock             單一實例鎖
  settings.json           第一次啟動時問的 app 網址(小幫手自己寫;state.json 歸啟動檔管,兩個分開)
  setup_core.py           launch.pyw 與 install.pyw 用的同一份安裝/啟動邏輯(標準函式庫)
  show.request            已經有一個在跑時,啟動檔留下它,那一個看到就把視窗叫到前面
  quit.request            重裝新版時 install.pyw 留下它,正在跑的那一個看到就結束(D21)
  versions\
    1.4.0\venv\           每個版本一個 venv(由安裝時那個 Python 建)
    1.5.0\venv\
    1.6.0.partial\        安裝到一半的新版(還沒驗過,啟動檔不會用它)
  profile\                小幫手自己的 Chrome 設定檔(放在版本外面,更新不會登出)
  logs\helper.log         記錄(輪替保留幾個檔),出事時請使用者傳這個
  history.jsonl           查詢紀錄(最近 200 筆,只在這台電腦;小視窗「查詢紀錄」分頁)
桌面\AI Workspace Helper.lnk  目標:<安裝時那個 pythonw.exe> "%LOCALAPPDATA%\AIWorkspaceHelper\launch.pyw"
啟動資料夾\AI Workspace Helper.lnk  同上,勾「開機時自動啟動」才有
```

`state.json`(用暫存檔 + `os.replace` 原子寫入,永遠不會寫一半):

```json
{
  "base_python": "C:\\...\\Python312\\pythonw.exe",
  "current": "1.6.0",
  "previous": "1.5.0",
  "start_at_login": true
}
```

〔D21〕`previous` 是重裝前的 `current`;有小幫手在跑時,安裝不刪任何版本資料夾(§3.7.5 第 1、2 點);`start_at_login` 記小視窗那個勾。
D15 時代的 `pending`、`pending_attempts`、`bad`、`failures`、`update` 不再寫;啟動檔不讀(只有 `take_over` 讀 `pending`,用來接手 0.1.0 下載的版本,§3.7.5 第 5 點),接手或下次安裝時清掉。全部的讀寫邏輯在 `ai_workspace_helper/setup_core.py`(只用
標準函式庫,同一個檔在套件裡、zip 根目錄、`launch.pyw` 旁邊各一份:打包放進 zip、安裝時(以及 `take_over` 接手時)放到 `launch.pyw` 旁,都是複製,不另外改)。

#### 3.7.2 發布新版(dev server → 後端)

1. 在 dev server 跑打包指令 `scripts/pack_ai_workspace_helper.py --version 1.6.0`,產出兩個檔:
   - `ai-workspace-helper-1.6.0.zip`:wheelhouse(3.10–3.14 各版本的 Windows wheel,版本釘死)+ `install.pyw` + `launch.pyw`;
   - `ai-workspace-helper.json`(版本資訊):`{"version": "1.6.0", "min_version": "1.4.0", "file": "ai-workspace-helper-1.6.0.zip", "sha256": "…"}`。
2. 把兩個檔放進後端設定 `server.ai_workspace_helper_dir` 指向的資料夾(掛載的 volume)。**不用重新部署**:後端每次請求
   直接讀這個資料夾。沒設或資料夾空 → 「小幫手」頁顯示尚未發布、卡片照手動流程。
3. 要強迫所有人升級(例如舊版有 bug):改 `ai-workspace-helper.json` 的 `min_version`。
4. 後端提供:`GET /api/ai-workspace-helper/manifest`(上面那份 json)、`GET /api/ai-workspace-helper/download/<file>`(zip;只給
   版本資訊裡列的那個檔,不能拿來讀資料夾裡別的東西)。都要登入,和其他路由一樣。

#### 3.7.3 小幫手和後端的版本約定

- 小幫手每個請求帶兩個標頭:`X-AI-Workspace-Helper-Version: 1.5.0`、`X-AI-Workspace-Helper-Protocol: 1`。
- 後端拒絕(回 **426**,內容附上版本資訊)的條件:
  - 協定號低於程式碼裡寫死的最低協定號——開發者改了不相容的 API 時調高它;
  - 或版本低於 `ai-workspace-helper.json` 的 `min_version`——運營方要強迫升級時調高它。
- API 只加不刪;小幫手看不懂的欄位一律忽略。要拿掉欄位,先調高最低協定號,讓讀它的舊版都被擋下、升級。

#### 3.7.4 有新版:只通知(D21)

小幫手啟動後第一次連上 AI Workspace 時(以及被停用後恢復連線時)、之後每 6 小時(`CHECK_EVERY_S`)讀版本資訊(`GET /api/ai-workspace-helper/manifest`);暫停、沒登入、連不上時不讀:

1. `version` 比自己新 → 小視窗狀態分頁多一行「有新版 1.6.0：請到「小幫手」頁下載，重新雙擊 install.pyw」和按鈕
   「**開啟下載頁**」;不比自己新 → 不顯示。小幫手照常領工作,什麼都不下載、不安裝。
2. 「開啟下載頁」用 Python 的 `webbrowser.open` 以**使用者的預設瀏覽器**開 `<網址>/ai-workspace-helper`
   ([webbrowser](https://docs.python.org/3/library/webbrowser.html):Windows 上交給系統的預設程式)。不用小幫手自己的 Chrome:Playwright 開的瀏覽器下載的檔,
   在那個 context 關掉時就被刪掉([Download](https://playwright.dev/python/docs/api/class-download)),而且下載要讓瀏覽器驗憑證,這正是 D21 的目的。
3. 讀版本資訊時連不上 → 「有新版」那一行維持上一次的樣子、「下載新版」(被停用)那一行收起來(連得上就不是被停用),原因進記錄檔(不含 cookie:Playwright 的錯誤只留「Call log」之前,錯誤鏈也切斷);被拒而成為 `BackendError` 的(404 還沒發布、403、5xx)→ 當成沒有新版、不顯示、不記;401/3xx/426 照它們自己的狀態走。都在 6 小時後再讀。
4. 為什麼是「一行字 + 一個按鈕」:這是要使用者知道、但不擋住目前工作的通知,附上能處理它的動作——GOV.UK 的
   notification banner([GOV.UK](https://design-system.service.gov.uk/components/notification-banner/))、NN/g 對「通知」的分類([NN/g](https://www.nngroup.com/articles/indicators-validations-notifications/));「開啟下載頁」是一個動作(把人帶到下載頁),畫成按鈕,和它那一行放在同一列——banner 也把動作放在通知裡。

#### 3.7.5 重新安裝:換上新版(D21)

`install.pyw` 跑在一個已經裝好的資料夾上(新版的 zip):

1. 照 D16 確認 Python,用它建 `versions\1.6.0\venv\`、離線裝套件、試跑(`--self-test`)——壞掉的新版在這一步就失敗,
   不會變成 `current`。**有小幫手在跑時,已經存在的版本資料夾一個都不碰**——鎖只說「有一個在跑」、不說是哪一版
   (Windows 鎖住執行中的檔案,§3.7 表):已經裝過的版本要重裝 → 先請人在小幫手的視窗按「結束」;新的版本照裝在旁邊。
   建一版要將近一分鐘,這段時間有人開了小幫手(捷徑)時,它跑的正是 `current`:所以真正刪掉舊的同版資料夾前再問一次,
   有小幫手在跑就停下、說明。
2. 成功 → `state.json` 寫 `current: 1.6.0、previous: 原來的 current`,複製 `launch.pyw` / `setup_core.py` 到根目錄(文字檔,
   沒在執行,換得掉);**沒有小幫手在跑時**才刪掉 `current`、`previous` 以外的版本資料夾(刪不掉的留著,下次再刪)。
3. 有小幫手正在跑(拿不到 `helper.lock`)→ 問「小幫手正在執行，要換成新版重新開啟嗎？」:
   - 是 → 寫 `quit.request`(和 `show.request` 同一個做法:正在跑的那個每 0.5 秒看一次),它照「結束」的路徑關掉 Chrome、
     放掉鎖、結束;`install.pyw` 等鎖(最多 20 秒,`launch(wait_s=…)`)後用 `current` 啟動新版。
   - 否 → 舊版照跑,下次開啟就是新版。
4. 沒有小幫手在跑 → 照原本的「現在開啟嗎？」。
5. **從 0.1.0 過來**:0.1.0 看不懂 `quit.request`,第 3 步等 20 秒後說明、請人在舊視窗按「結束」(0.1.0 的「立即更新」帶 `--wait`
   啟動根目錄的 `launch.pyw`,新的啟動檔照樣等它結束)。另外,連得上 AI Workspace 的 0.1.0 仍會照 D15 自己下載下一版、記成
   `pending`,由它的啟動檔啟動——而新版不再「確認自己」,三次後會被那個舊啟動檔退回且不再試。
   所以新版每次啟動時(拿到鎖之後)看一次:自己就是 `pending` 那一版 → 直接接手(`take_over`:`current` = 自己、`previous` = 原
   `current`、D15 的欄位清掉、其他版本資料夾刪掉、把 D15 放在自己資料夾裡的 `launch.pyw`/`setup_core.py` 換到根目錄;
   `pending` 剛好就是 `current`(重裝後舊版才準備好同一版)時只清欄位,`previous` 不動)。
6. 新版在舊版剛放掉鎖時就啟動,舊的 Chrome 可能還沒放開設定檔:開 Chrome 失敗會隔 2 秒再試,共 3 次,才顯示「開不了 Chrome」。

#### 3.7.6 被要求更新(426,D21)

- 收到 426:小視窗狀態「這個版本已停用，需要更新」,下一行「請到「小幫手」頁下載新版 X，重新雙擊 install.pyw」
  (426 沒指名版本時不寫 X)+「開啟下載頁」,不再領工作。管理員把 `min_version` 調回去後,下一次連上就馬上重讀版本資訊:
  發布的版本仍比自己新 → 換成「有新版 X」;不比自己新 → 那一行消失。
  卡片照 D7 退回手動流程,所以這段時間使用者仍然查得到東西。重裝(§3.7.5)之後新版接手。
- 426 也可能是偽造的(D20:那條連線不驗憑證):最壞是小幫手停工、人被帶去下載頁,不會執行任何東西。

#### 3.7.7 其他

- **心跳**附上小幫手版本、協定號、Chrome 版本、Playwright 版本、Python 版本(〔D21〕不再送「上一次更新結果」;後端欄位照 §3.7.3 只加不刪,留著)——後端看得到誰還在舊版、誰的 Chrome
  已經超過測過的版本。
- **Chrome 設定檔**在 `profile\`,單一實例鎖保證不會兩個小幫手同時用;壞掉時小視窗有「重設登入」(移開設定檔、重新登入)。
- **第一次安裝**:D16,`install.pyw` 建好上面的結構與捷徑;〔D21〕之後每一版也都用它裝(§3.7.5)。
- **解除安裝**(D17):小視窗按「解除安裝」→ 確認「會刪掉小幫手和它的登入資料」→ 小幫手放掉 `helper.lock`、結束 Chrome,
  啟動一個收尾程序(用 `base_python`,程式碼先複製到 `%TEMP%`,因為正在跑的檔案刪不掉)後結束自己;收尾程序等小幫手
  的行程結束,刪掉兩個捷徑與 `%LOCALAPPDATA%\AIWorkspaceHelper\`,最後刪掉自己在 `%TEMP%` 的檔案。刪不掉的(例如被防毒鎖住)
  寫進 `%TEMP%\ai-workspace-helper-uninstall.log` 並跳視窗列出位置,讓人手動刪。
  說明頁寫明手動解除安裝 = 刪掉這三樣:`%LOCALAPPDATA%\AIWorkspaceHelper\`、桌面與啟動資料夾的 `AI Workspace Helper.lnk`。

## 4. Phases

| Phase | 內容 | 完成的判準 |
|---|---|---|
| P1 | 這份計劃 | 文件測試、mkdocs `--strict` 綠 |
| P2 | `OutsideJob` resource + 卡片路由(建立/讀/取消)+ 小幫手路由(心跳/領/交失敗、逾時回收) | 只領自己的工作;CAS 不雙領;逾時回 `queued`;權限同送訊息 |
| P3 | 交結果 = 送給 AI:經既有 outside-answers 存檔 + 以按「查」的人送 `answers` 訊息;存檔時抽出內容裡的連結記為查過的網址,`ask_outside(url=…)` 比對後自動建工作(D12) | 與手動送出走同一段程式(parity 測試);檔頭記來源網址;同一張卡不會送兩次;查過的網址自動開、沒查過的等「查」;手動貼上的也算;錨點不同視為同一網址 |
| P4 | 卡片:連線狀態、交給小幫手、進行中/取消/失敗;沒連線退回手動 | 小幫手沒連線時卡片與今天一模一樣 |
| P5 | `ai-workspace-helper/` 骨架:小視窗(D13)、Chrome persistent context、登入與過期重登、領工作迴圈、差事外掛介面、驗證碼等待 | 對本機假後端 + 本機假網站跑通(Linux) |
| P6 | 差事 `search` / `open_url`:開頁、整頁 HTML 經打包的 `htmlToMarkdown` 轉 Markdown | parity:範例 HTML 在前端與小幫手轉出逐字相同;對本機假搜尋頁抓得到結果連結 |
| P7 | 安裝包:`scripts/pack_ai_workspace_helper.py`(D14、§3.7.2,產 `ai-workspace-helper-<版本>.zip` + `ai-workspace-helper.json`)、`install.pyw`(D16)、AI Workspace 的下載頁 | 在 Linux 上產出的安裝包含小幫手與 Playwright、greenlet cp310–cp314 的 win_amd64 wheel,版本釘死;`pip install --no-index --dry-run` 對 win_amd64 解得出全部依賴;下載頁下載得到 |
| P8 | 〔其中的背景更新、換版與退回、強制更新的自動下載由 P12–P14 拿掉(D21);§3.7.4–3.7.6 已改寫〕更新(§3.7)與解除安裝(D17):`ai_workspace_helper_dir` 與版本資訊/下載路由(§3.7.2)、426 與協定號(§3.7.3)、背景更新(§3.7.4)、`launch.pyw` 換版與退回(§3.7.5)、強制更新(§3.7.6)、心跳回報版本 | 版本資訊只列出的檔可下載;低於 `min_version` 或最低協定號回 426;下載 sha256 不符不裝;任一步失敗現在這版照常跑、同版連 3 次失敗進 `bad`;`pending` 3 次沒確認自動退回;`state.json` 只用原子寫入;已有一個在跑時不開第二個;`launch.pyw` 只在新版確認後才被換掉。解除安裝刪掉三樣、刪不掉的列出來。全部在 Linux 上用假的 Python 與假的後端測 |
| P9 | 文件:`docs/migrations.md`(新 resource、新路由、新下載物、IT 要放行的項目)、使用者說明 | mkdocs `--strict` 綠 |
| P10 | 〔其中的「一次更新(換指標、下次開啟生效)」由 P14 改成「在舊版上重裝新版」(D21)〕live check(真 app + 真 Chrome + 本機假網站)+ demo MP4 | 搜尋與開網址各走一次:按「查」之後不再經人,`lookups/` 有檔、AI 讀到;一次更新(換指標、下次開啟生效) |
| P11 | review(四把鏡頭)→ CI | 照 CLAUDE.md |
| P12 | 〔D21〕更新只通知:`Updater` 讀版本資訊,比自己新 → 狀態「有新版 X」;426 → 「這個版本已停用」;小視窗那一行 + 「開啟下載頁」(`webbrowser.open(<網址>/ai-workspace-helper)`);不下載、不安裝 | 新版 → 顯示、照常領工作;同版或舊版 → 不顯示;讀不到 → 不顯示、記 log;426 → 停領工作 + 顯示;按鈕開的網址帶部署前綴;小幫手沒有任何下載或安裝的呼叫 |
| P13 | 〔D21〕重裝換版:`install.pyw` 在已安裝的資料夾上裝新版(`previous` = 原 `current`,刪其餘版本資料夾);有小幫手在跑時問、寫 `quit.request`、等鎖、啟動新版 | 新版試跑失敗 → `current` 不變;正在跑的版本資料夾不刪;`quit.request` 讓正在跑的那個走「結束」的路徑(Chrome 關、鎖放掉);等不到鎖 → 說明、不開第二個;不開 → 舊版照跑 |
| P14 | 〔D21〕拿掉自動更新(從 0.1.0 過來的 `take_over` 見 §3.7.5 第 5 點):`prepare_update` / `update_outcome` / 解壓、`pending`/`bad`/`failures`/`update` 與 `launch.pyw` 的換版退回、`confirm`、`Backend.download` / `Chrome.fetch_bytes`、「立即更新」(啟動檔仍認得 `--wait`,給 0.1.0 的「立即更新」用,§3.7.5 第 5 點)、心跳的 `update`、live check 的更新那半(改成「重裝換版」) | 程式裡不再有寫這些欄位或下載 zip 的路徑;舊 `state.json` 有這些欄位照樣啟動;測試跟著刪改,不留沒人用的程式 |
| P15 | 文件:使用說明「更新」、`docs/migrations.md` #933(行為改變:不再自動更新,每一版都要重裝)、PR 說明 | mkdocs `--strict` 綠;每句寫在驗證之後 |
| P16 | review(conformance / veracity / defect / regression)→ CI | 照 CLAUDE.md;P12–P14 換掉一個機制,review 至少一輪 |
| P17 | 〔D22〕差事等跳轉停下來才讀頁;空頁以中文說明失敗 | 真 Chromium 對假網站:腳本→自送表單→302 的 SSO 式跳轉讀到最後一頁;有字的轉址頁不被當成答案;每跳都在 1.5 秒內但總長超過的跳轉鏈跟到底;表單回應很慢時不讀到送出前那頁;晚出現內容的空白頁等得到;永遠空白的頁以「沒有文字」失敗;驗證碼照舊等人 |
| P18 | review → CI | 照 CLAUDE.md |

## 5. 不做(這一版)

- 「搜尋」「開網址」以外的差事(D9 的介面先留著)。
- 一台共用的小幫手(每個人跑自己的;D6 的身分就是登入的那個人)。
- Google 以外目的地的結果解析(其他目的地照舊走手動按鈕)。
- 自動判斷結果有沒有回答問題(由 AI 下一輪讀)。

## 6. 已知的縫

- **不經人的網址只限查過的**(D12):AI 自己寫的網址一律等人點頭;直接開的只有先前查回來的內容裡本來就有的連結。
  內容是人手動貼的時候,裡面的連結也算查過——那是人自己貼進來、看過的東西。
- **「只為發問的人自動開」靠的是訊息順序**(§3.2):發問者 = 卡片之前最後一則使用者訊息的作者。共用 item 裡兩個人的
  訊息排隊時(Q1、Q2 先存、答案後到),那個人可能認錯——和 CLAUDE.md 記的「排隊的對話讀起來是 Q1、Q2、A1、A2」
  同一個原因:訊息上沒有記它屬於哪一輪。被開的只會是先前查回來的公開連結。
- **共用 item 裡,別人看得到你的小幫手正在查,也按得到「取消」**:取消照卡片的權限(`converse`),不限工作的主人。
- **兩個 `install.pyw` 同時跑**(同一版)共用 `versions\<版本>.partial\`:可能兩個都失敗,或其中一個裝成另一個做到一半的 venv;不同版時,先完成且沒有小幫手在跑的那個會清掉另一個的 `.partial`。沒有鎖,因為這要人同時雙擊兩次。
- **交答案的請求在記下連結、送出訊息之前被中斷**(連線斷掉):`lookups/` 留下一個沒有訊息指著的檔;連結也已記下,
  後面的卡片開它不再問。這個窗口只有記連結那一下。

## 7. prod 怎麼驗證

1. 從 app 的「小幫手」頁下載 zip、解壓縮,先雙擊 `precheck.pyw`(D19):六項都 ✔ 再往下;沒通過的那一項就是公司電腦擋住的地方。
   雙擊 `install.pyw`:確認顯示的 Python、按「安裝」,桌面出現 `AI Workspace Helper` 捷徑。雙擊捷徑:小視窗出現;
   按「登入」開 Chrome 登入 app,視窗顯示「已連線，等待查詢」。這一步是 Windows 上唯一的驗證(D14):裝不裝得起來、`.pyw` 能不能雙擊、
   捷徑開不開得起來、Chrome 開不開得出來、公司代理與 SSO。
2. 在聊天問一個需要外部資料的問題;卡片上方顯示小幫手已連線,主要按鈕是「查」。
3. 改一下查詢、按「查」:小幫手的 Chrome 搜尋改過的字;不用再按別的,`lookups/` 多一個檔(搜尋結果頁),AI 接著讀。
4. AI 想看結果頁上的其中一頁時出「開網址」卡片;不用按,小幫手直接開那一頁送給 AI,AI 引用它作答。
5. 叫 AI 開一個它自己寫的網址(不在先前的結果裡):卡片等你按「查」。
6. 關掉小幫手再問一次:卡片回到手動流程(Google 按鈕 + 貼上區)。
