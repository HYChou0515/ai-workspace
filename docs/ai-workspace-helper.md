# 小幫手(AI Workspace Helper)

「請幫我查」卡片(`ask_outside`,見 [plan-outside-lookup](plan-outside-lookup.md))原本要使用者自己開瀏覽器查、
複製、貼回來。**小幫手**(正式名稱 AI Workspace Helper,桌面捷徑也叫這個名字)是裝在使用者自己電腦上的小程式:它用使用者電腦上的 Chrome 開網頁,把整頁轉成
Markdown 交回卡片——使用者只要在搜尋時按一下「查」。設計與取捨在 [plan-outside-helper](plan-outside-helper.md)。

- 只有**搜尋**要按「查」(搜尋框裡的關鍵字由你自己打或貼上,AI 的建議在旁邊可以複製);**開網址**時,若那個網址出現在這個聊天先前查回來的內容裡,
  小幫手直接開,不再問;AI 自己寫出的網址照樣要按「查」。
- 小幫手沒開、暫停、登入過期時,卡片照以前的方式讓使用者自己查、自己貼,只多一行「小幫手未連線」和「怎麼安裝」連結(這個部署沒發布小幫手時連這行都沒有)。
  小幫手正在處理這張卡片時(例如等你完成驗證),卡片照樣顯示進度。
- 網站要人機驗證時(Google 的「異常流量」、reCAPTCHA、hCaptcha、Cloudflare 的「Just a moment…」),小幫手把 Chrome 視窗叫到前面、卡片顯示「請在小幫手的 Chrome 視窗裡完成驗證」,最多等 5 分鐘;你完成驗證、頁面跳到內容後它才讀。Cloudflare 直接封鎖的網站(錯誤 1020 等)人也過不去,小幫手會直接說,請自己查、貼上內容(plan D25)。
- 抓回來的頁面存進 workspace 的 `lookups/`,檔頭記著「由小幫手抓取」與來源網址。
- 存檔的內容原樣不動,裡面的網頁連結另外編號列在檔案最下面(`- [n] <網址> 標題`;Google 的跳轉連結列成它導向的那一頁);AI 要開其中一個時給檔名和編號,不必照抄網址(plan D24)。你自己貼上的內容也一樣(你能在這個項目新增檔案時;不能的話不存檔,也就沒有清單)。

## 使用者:安裝(每台電腦一次)

需要:Windows、電腦上有 Python 3.9–3.14、有 Google Chrome。不用連外網裝套件,安裝包裡都有。
**不需要系統管理員權限**:不跑安裝程式、不寫 Program Files、不改登錄檔、不跳 UAC——只寫你自己的
`%LOCALAPPDATA%\AIWorkspaceHelper\`(用電腦上的 Python 建一個 venv、從 zip 裡的套件離線裝好)和你自己桌面上的捷徑。
但公司電腦可能不讓它**執行**:見下面的預檢與「IT 要放行的」。

1. 在 AI Workspace 左側選單打開「**小幫手**」,下載 `ai-workspace-helper-<版本>.zip`。
2. 解壓縮,先雙擊 **`precheck.pyw`**(預檢):它**不安裝任何東西**,只在 `%LOCALAPPDATA%` 旁邊的暫存資料夾裡把小幫手
   需要的每一樣真的試一次,結束就刪掉,最後列一張表:

   | 檢查 | 試的是 | 沒通過代表 |
   |---|---|---|
   | Python 3.9 以上 | 執行它的那個 Python | 換一個 Python(`py -3.12 precheck.pyw`) |
   | 在使用者資料夾執行程式 | 在 `%LOCALAPPDATA%` 建 venv 並執行它 | 公司政策(例如 AppLocker)不讓使用者資料夾裡的程式執行;請 IT 放行 `%LOCALAPPDATA%\AIWorkspaceHelper\` |
   | Playwright 的 node.exe | 從 zip 裡取出 Playwright 自帶的 `node.exe` 執行 | 防毒或政策擋下它;請 IT 放行 |
   | 建立桌面捷徑(PowerShell) | 用安裝時同一段 PowerShell 建一個捷徑,並確認檔案真的出現 | PowerShell 被限制;**安裝照樣完成**,之後從 `%LOCALAPPDATA%\AIWorkspaceHelper\launch.pyw` 開 |
   | Chrome | 已安裝的 `chrome.exe` | 先裝 Chrome |
   | Chrome 允許自動化 | 公司的 Chrome 政策 `RemoteDebuggingAllowed` | 被政策禁止;請 IT 放行 |

   有沒通過的,按「**複製結果**」貼給 IT 或系統管理員。全部通過再往下。
3. 雙擊 **`install.pyw`**。它會先說「將使用 Python 3.x(完整路徑)」——電腦上有好幾個 Python 而
   這不是你要的,按「取消」,在那個資料夾開命令列改用 `py -3.12 install.pyw`(換成你要的版本)。
4. 裝好後桌面有「**AI Workspace Helper**」(PowerShell 被限制時沒有捷徑,安裝結束的訊息會寫要雙擊哪個檔)。雙擊它;第一次會問 AI Workspace 的網址(「小幫手」頁上寫著,照抄)。
5. 它會開一個**自己的** Chrome 視窗(和你平常的 Chrome 分開,登入資料也分開)。在那裡照平常方式登入 AI Workspace
   一次,小視窗變成「已連線，等待查詢」。之後登入過期時,它會自己把那個 Chrome 視窗叫到前面、開登入頁(開著的時候過期,小視窗寫「登入已過期」;重開時已經過期,寫「請在小幫手開的 Chrome 視窗登入」)。

裝好之後,**下載的 zip 和解壓縮出來的資料夾都可以刪掉**:小幫手本身在 `%LOCALAPPDATA%\AIWorkspaceHelper\`(別刪它),
桌面的「AI Workspace Helper」是指向它的捷徑。不要把 `launch.pyw` 單獨搬到別處——它要和旁邊的 `setup_core.py` 在一起;
捷徑也寫死了安裝時確認的 Python,比直接雙擊 `.pyw` 可靠。要預檢或重裝時,再從「小幫手」頁下載一次。

小視窗有三個分頁:

- **狀態**:現在在做什麼——「已連線，等待查詢」「正在搜尋（Google）：…」「正在開啟：https://…」,和按鈕:
  「登入」(手動叫出登入頁)、「暫停/繼續」、「結束」、「開機時自動啟動」、「重設登入」(登入一直不成功時:
  把它的 Chrome 設定檔移到旁邊、重新開一個再登入)、「解除安裝」;有新版、或這一版被停用時,多一行說明和「開啟下載頁」。
- **查詢紀錄**:最近 200 筆——時間、類型(搜尋/開啟網頁)、內容、結果(已送給 AI/沒查到：原因)。
  只存在這台電腦(`%LOCALAPPDATA%\AIWorkspaceHelper\history.jsonl`),不送回 AI Workspace;解除安裝時一起刪掉。
- **記錄檔**:`helper.log` 的最後幾百行,和「開啟記錄資料夾」。
不小心關掉它開的 Chrome 視窗也沒關係,它會再開一個。
關掉小視窗就是結束小幫手。

## 使用者:平常

- 卡片出現時,搜尋按「**查**」;開網址看卡片上的完整網址,沒查過的才要按「查」。卡片會顯示「小幫手正在查…」,
  好了就收起來,AI 接著讀;存在哪個檔(`lookups/…`)寫在它送出的那則訊息裡。重新整理頁面也看得到進行中的工作,
  不會重查一次。小幫手中途被關掉(電腦闔上),兩分鐘後卡片就退回「未連線」,可以自己查。
- 遇到「我不是機器人」之類的驗證,小幫手把它的 Chrome 視窗叫到前面,卡片顯示「請在小幫手的 Chrome 視窗裡完成驗證」。
  在那個視窗完成驗證,它就繼續;5 分鐘沒完成,這次就算失敗,卡片可以「重查」或「自己查」。
- 一次只做一件。頁面打不開(例如 404)、逾時,卡片會寫原因。

## 使用者:更新

小幫手**不會自己下載或安裝新版**(它和 AI Workspace 之間不驗憑證,見「IT 要放行的」;plan D21)。啟動後第一次連上 AI Workspace 時(以及被停用後恢復連線時)、
之後每 6 小時它看一次有沒有新版,有的話小視窗多一行「有新版 X：請到「小幫手」頁下載，重新雙擊 install.pyw」和「**開啟下載頁**」按鈕——
按下去用你的預設瀏覽器開「小幫手」頁。下載、解壓縮、雙擊 `install.pyw`:

- 新版裝在另一個資料夾、先試跑,**不碰正在用的這一版**;試跑失敗就留在舊版。
- 小幫手正開著時,它會問「要換成新版重新開啟嗎？」——按「是」,舊的那個會自己結束、換成新版。
  從 0.1.0 換上來時,舊版看不懂這個要求:等 20 秒後它會請你在舊視窗按「結束」,再開小幫手就是新版。
- 小幫手正開著時重裝**已經裝過的版本**(例如同一版修復):它會請你先在小幫手的視窗按「結束」再裝。
- 只留現在這版和上一版的資料夾(小幫手開著時先不清,下次沒開著時安裝再清)。

管理員停用了你這一版:小視窗寫「這個版本已停用，需要更新」,一樣有「開啟下載頁」,不再接工作;這段時間卡片照舊讓你自己查。

## 使用者:解除安裝

小視窗按「**解除安裝**」並確認。它會結束自己,刪掉桌面與「啟動」資料夾裡的「AI Workspace Helper」捷徑,和
`%LOCALAPPDATA%\AIWorkspaceHelper\`(包括它自己的 Chrome 登入資料)。刪不掉的(例如被防毒鎖住)會跳視窗列出來,
清單也寫在 `%TEMP%\ai-workspace-helper-uninstall.log`。

手動解除安裝 = 刪掉這三樣:`%LOCALAPPDATA%\AIWorkspaceHelper\`、桌面的 `AI Workspace Helper.lnk`、
「啟動」資料夾(`shell:startup`)的 `AI Workspace Helper.lnk`。

## 問題排除

| 小視窗寫 | 是什麼 | 怎麼辦 |
|---|---|---|
| 請在小幫手開的 Chrome 視窗登入／登入已過期 | 還沒登入過,或部署的登入過期、被導到 SSO 頁 | 在它的 Chrome 視窗登入;登入前交給它的工作會等著,不會丟 |
| 連不上 AI Workspace，會自動重試 | 網址錯、或公司網路不通;原因寫在狀態的第二行(0.1.1 起也記在記錄檔) | 檢查第一次填的網址(存在 `%LOCALAPPDATA%\AIWorkspaceHelper\settings.json`)。第二行是 `unable to verify the first certificate` 之類的憑證錯誤:你的小幫手是 0.1.0,請從「小幫手」頁下載新版、重新雙擊 `install.pyw` |
| 沒查到:這一頁轉出來沒有文字(可能還在跳轉、是 PDF 或內嵌框架、或要登入) | 小幫手等到那一頁停下來,上面還是沒有任何文字或連結:可能要另外登入那個網站、是 PDF(小幫手只讀網頁)、或內容放在內嵌框架裡 | 在小幫手開的 Chrome 視窗自己開那個網址看看是什麼;要登入的網站在那個視窗登入一次後再查。舊版小幫手看到的是「nothing to send — paste or attach」,常是那時還沒等跳轉結束,請更新 |
| 沒查到:這一頁一直在跳轉,30 秒內沒有停下來 | 那個網址一直在轉址(例如登入一直繞回原點),或某一步的伺服器一直沒回應 | 在小幫手開的 Chrome 視窗自己開那個網址看看停在哪;登入繞圈時,按「重設登入」後重新登入 |
| 開不了 Chrome | 沒裝 Chrome、Chrome 剛更新超過小幫手支援的版本、Chrome 被政策擋下自動化、或上一個小幫手還沒關完 | 更新小幫手;按「重設登入」;見下面「IT 要放行的」;或等一下再開 |
| 這個版本已停用 | 管理員調高了最低版本 | 見「更新」 |

出問題時請附上 `%LOCALAPPDATA%\AIWorkspaceHelper\logs\helper.log`(「記錄檔」分頁的「開啟記錄資料夾」)。

## 運營方:發布一版

在 dev server(要連得到 PyPI,並有 `pnpm`、`uv` 與一個裝了 pip 的 Python):

```bash
python3 scripts/pack_ai_workspace_helper.py --version 0.1.0 [--min-version 0.1.0]
```

它做的事:

- 從前端原始碼建出卡片用的轉換器;
- 用版本號建小幫手的 wheel(原始碼不會被改);
- 對 Python 3.9–3.14 各下載一次 Windows wheel,每個套件版本釘死;
- 最後對每個 Python 都跑一次 `pip install --dry-run --no-index`,證明只靠安裝包就裝得起來。

輸出在 `dist/ai-workspace-helper/`:`ai-workspace-helper-<版本>.zip` 與 `ai-workspace-helper.json`。把這兩個檔放進
`server.ai_workspace_helper_dir` 指的資料夾([設定](configuration.md)),**不用重新部署**。要強迫所有人升級:改
`ai-workspace-helper.json` 的 `min_version`。

小幫手帶的是 Playwright 1.60.0(驅動已安裝的 Chrome,不另外下載瀏覽器),每個 Python 都同一版:1.60.0 是最後一個
裝得上 Python 3.9 的版本(1.61 起要 3.10),而有些人的電腦只有 3.9(plan D23)。只有 greenlet 依 Python 不同:3.9 用 3.2.4
(它最後一個有 cp39 Windows wheel 的版本),3.10–3.14 用 3.5.6。這個 repo 的測試環境因為 Debian 11 的瀏覽器下載限制停在 1.49,
所以**驅動瀏覽器的測試跑的是 1.49、發出去的是 1.60**——1.49 需要的 greenlet 沒有 Python 3.14 的 Windows 版,無法涵蓋 3.14。
1.60 這一版在 Linux 的 CPython 3.9 上驗過:`setup_core.install` 只靠 wheelhouse 裝起來、`--self-test` 通過、開分頁讀頁。
這個 repo 的測試跑在 3.12,所以 `tests/ai_workspace_helper/test_python39.py` 用 3.9 的語法讀小幫手發出去的每個檔,
點名 3.9 不認得的語法與常見的新 API(例如 `zip(strict=…)`)。Windows 上的實際驗證由人在公司筆電上做一次(plan D14)。

小幫手每次報到會回報它的版本、Chrome、Playwright、Python 版本,存在後端(`_AiWorkspaceHelperPresence`)。
小幫手**不會自己更新**(plan D21):發布新版後,使用者的小視窗會在啟動後第一次連上 AI Workspace 時(以及被停用後恢復連線時)、或之後 6 小時內顯示「有新版」,由使用者自己下載重裝;
要強迫升級就改 `min_version`,低於它的小幫手停止接工作、顯示「這個版本已停用」。

## IT 要放行的

小幫手**不需要系統管理員權限**;下面是它需要被允許**執行**的東西。`precheck.pyw` 逐項試給你看。

- **使用者資料夾裡的程式**:小幫手的 venv(`python.exe`/`pythonw.exe`)和 Playwright 的 `node.exe` 都在
  `%LOCALAPPDATA%\AIWorkspaceHelper\` 底下。AppLocker 的預設規則只放行 Program Files 與 Windows 資料夾
  ([AppLocker default rules](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/applocker/understanding-applocker-default-rules)),這種電腦要對這個資料夾(或依簽章)加一條允許規則。
- **下載**:安裝包是 zip,但裡面的 Playwright 套件包著 `node.exe`;擋 exe 下載的代理或防毒可能擋下或刪掉它
  (預檢的「Playwright 的 node.exe」會失敗)。
- **PowerShell**:安裝時用 PowerShell 建桌面捷徑(`New-Object -ComObject WScript.Shell`);
  受限語言模式不允許([about_Language_Modes](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_language_modes))。這一項不影響安裝,只是沒有捷徑。
- **防毒**:Playwright 會啟動它自帶的 `node.exe`(在 `%LOCALAPPDATA%\AIWorkspaceHelper\versions\<版本>\venv\` 底下)。
  企業防毒擋過 Playwright 的執行檔([playwright-java#1016](https://github.com/microsoft/playwright-java/issues/1016))。
  被擋時小視窗會是「開不了 Chrome」或連不上;請依資料夾或簽章放行一次。
- **Chrome 政策**:小幫手透過 Chrome 的自動化介面驅動它;公司若用政策停用 Chrome 的遠端偵錯/自動化
  (`RemoteDebuggingAllowed` = 0,[Chrome 政策](https://chromeenterprise.google/policies/#RemoteDebuggingAllowed)),小幫手開不了 Chrome
  ([Playwright: browsers](https://playwright.dev/python/docs/browsers))。
- **憑證**:小幫手跟 AI Workspace 講話(報到、領工作、交結果、讀版本資訊)的請求不是 Chrome 送的,是 Playwright 自帶的 Node
  送的;Node 不讀 Windows 的憑證庫([Node: --use-system-ca](https://nodejs.org/api/cli.html#--use-system-ca)),也不會去補伺服器沒送的中繼憑證,所以 Chrome 驗得過的
  公司憑證它驗不過。小幫手對**這些請求**一律不驗憑證、沒有開關(依這個部署的慣例 verify=false,[Playwright: fetch 的
  `ignore_https_errors`](https://playwright.dev/python/docs/api/class-apirequestcontext#api-request-context-fetch));它替你開的外面網頁仍由 Chrome 照常驗。**代價**:能在公司網路上攔截這條連線的人,看得到登入 cookie、可以假造工作與版本資訊。
  **更新不走這條連線**(plan D21):小幫手只通知有新版,zip 由你的瀏覽器(會驗憑證)下載、你自己執行 `install.pyw`,
  所以假造的版本資訊最多把你帶去下載頁、或讓小幫手停止接工作,不會讓它執行任何東西。
- **網路**:小幫手只連 AI Workspace(工作與版本資訊從那裡拿)與它替你開的網頁;新版的 zip 由你的瀏覽器下載,和第一次安裝一樣。
