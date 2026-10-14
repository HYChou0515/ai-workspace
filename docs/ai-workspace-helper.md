# 小幫手(AI Workspace Helper)

「請幫我查」卡片(`ask_outside`,見 [plan-outside-lookup](plan-outside-lookup.md))原本要使用者自己開瀏覽器查、
複製、貼回來。**小幫手**(正式名稱 AI Workspace Helper,桌面捷徑也叫這個名字)是裝在使用者自己電腦上的小程式:它用使用者電腦上的 Chrome 開網頁,把整頁轉成
Markdown 交回卡片——使用者只要在搜尋時按一下「查」。設計與取捨在 [plan-outside-helper](plan-outside-helper.md)。

- 只有**搜尋**要按「查」(查詢字可以先改);**開網址**時,若那個網址出現在這個聊天先前查回來的內容裡,
  小幫手直接開,不再問;AI 自己寫出的網址照樣要按「查」。
- 小幫手沒開、暫停、登入過期時,卡片照以前的方式讓使用者自己查、自己貼,只多一行「小幫手未連線」和「怎麼安裝」連結(這個部署沒發布小幫手時連這行都沒有)。
  小幫手正在處理這張卡片時(例如等你完成驗證),卡片照樣顯示進度。
- 抓回來的頁面存進 workspace 的 `lookups/`,檔頭記著「由小幫手抓取」與來源網址。

## 使用者:安裝(每台電腦一次)

需要:Windows、電腦上有 Python 3.10–3.14、有 Google Chrome。不用連外網裝套件,安裝包裡都有。
**不需要系統管理員權限**:不跑安裝程式、不寫 Program Files、不改登錄檔、不跳 UAC——只寫你自己的
`%LOCALAPPDATA%\AIWorkspaceHelper\`(用電腦上的 Python 建一個 venv、從 zip 裡的套件離線裝好)和你自己桌面上的捷徑。
但公司電腦可能不讓它**執行**:見下面的預檢與「IT 要放行的」。

1. 在 AI Workspace 左側選單打開「**小幫手**」,下載 `ai-workspace-helper-<版本>.zip`。
2. 解壓縮,先雙擊 **`precheck.pyw`**(預檢):它**不安裝任何東西**,只在 `%LOCALAPPDATA%` 旁邊的暫存資料夾裡把小幫手
   需要的每一樣真的試一次,結束就刪掉,最後列一張表:

   | 檢查 | 試的是 | 沒通過代表 |
   |---|---|---|
   | Python 3.10 以上 | 執行它的那個 Python | 換一個 Python(`py -3.12 precheck.pyw`) |
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

小視窗有三個分頁:

- **狀態**:現在在做什麼——「已連線，等待查詢」「正在搜尋（Google）：…」「正在開啟：https://…」,和按鈕:
  「登入」(手動叫出登入頁)、「暫停/繼續」、「結束」、「開機時自動啟動」、「重設登入」(登入一直不成功時:
  把它的 Chrome 設定檔移到旁邊、重新開一個再登入)、「解除安裝」;新版備好時多一顆「立即更新」。
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

不用自己做。小幫手開啟時與之後每 6 小時檢查一次新版,有就下載(這幾秒不接新工作)、在背景裝在另一個資料夾、試跑——**不碰正在用的這一版**。
好了小視窗寫「新版已下載，下次開啟時套用」,下次開啟就換上。

- 新版開不起來:連續 3 次開啟都沒跑起來(開不了 Chrome、連不到 AI Workspace、或後端拒絕它的請求),啟動檔自動退回舊版,
  這個新版不再試(等下一版)。只是沒登入或暫停著不算,不會因此退回。
- 管理員停用了你這一版:小視窗寫「這個版本已停用，需要更新」,同時馬上準備新版,好了按「**立即更新**」。
  這段時間卡片照舊讓你自己查。更新失敗時,回「小幫手」頁重新下載、執行 `install.pyw`。

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
| 連不上 AI Workspace，會自動重試 | 網址錯、或公司網路不通;原因寫在狀態的第二行,也記在記錄檔 | 檢查第一次填的網址(存在 `%LOCALAPPDATA%\AIWorkspaceHelper\settings.json`)。第二行是 `unable to verify the first certificate` 之類的憑證錯誤:你的小幫手是 0.1.0,請從「小幫手」頁下載新版、重新雙擊 `install.pyw` |
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
- 對 Python 3.10–3.14 各下載一次 Windows wheel,每個套件版本釘死;
- 最後對每個 Python 都跑一次 `pip install --dry-run --no-index`,證明只靠安裝包就裝得起來。

輸出在 `dist/ai-workspace-helper/`:`ai-workspace-helper-<版本>.zip` 與 `ai-workspace-helper.json`。把這兩個檔放進
`server.ai_workspace_helper_dir` 指的資料夾([設定](configuration.md)),**不用重新部署**。要強迫所有人升級:改
`ai-workspace-helper.json` 的 `min_version`。

小幫手帶的是 Playwright 1.63.0(驅動已安裝的 Chrome,不另外下載瀏覽器)。這個 repo 的測試環境因為 Debian 11 的
瀏覽器下載限制停在 1.49,所以**驅動瀏覽器的測試跑的是 1.49、發出去的是 1.63**——1.49 需要的 greenlet 沒有 Python 3.14
的 Windows 版,無法涵蓋 3.14。1.63 這一版在 Linux 上驗過安裝、換版與試跑(`--self-test`:Playwright 載得起來、
它的 driver 在):`scripts/check_ai_workspace_helper.py --update-wheels <1.63 的 Linux wheel>`。Windows 上的實際驗證由人
在公司筆電上做一次(plan D14)。

小幫手每次報到會回報它的版本、Chrome、Playwright、Python 版本與上一次更新的結果,存在後端(`_AiWorkspaceHelperPresence`)。

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
- **憑證**:小幫手跟 AI Workspace 講話(報到、領工作、交結果、下載更新)的請求不是 Chrome 送的,是 Playwright 自帶的 Node
  送的;Node 不讀 Windows 的憑證庫([Node: --use-system-ca](https://nodejs.org/api/cli.html#--use-system-ca)),也不會去補伺服器沒送的中繼憑證,所以 Chrome 驗得過的
  公司憑證它驗不過。小幫手對**這些請求**不驗憑證(和部署裡其他內部呼叫一樣 verify=false,[Playwright: fetch 的
  `ignore_https_errors`](https://playwright.dev/python/docs/api/class-apirequestcontext#api-request-context-fetch));它替你開的外面網頁仍由 Chrome 照常驗。更新檔另外比對 sha256。
- **網路**:小幫手只連 AI Workspace(工作、更新都從那裡拿)與它替你開的網頁;更新檔不經瀏覽器下載,不會被標成「從網路下載」。
