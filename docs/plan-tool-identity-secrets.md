# 工具用自己的身分執行：AI 讀不到工具的原始碼，也拿不到工具的帳密

**狀態：討論中（2026-11-16），尚未核准施工。** 這份是盤點與方向，不是可以直接照做的施工單——「上線前要先驗證」那一節的事情沒做完之前不動工。

來源標記：**〔user〕** = user 決定；**〔查證〕** = 讀 `origin/master`（`1f0056c8`）程式碼確認的事實，附位置；**〔提案〕** = 我提的、user 說「看起來可以」但還沒逐條定案。

## 問題

user：「我希望 agent 不能讀取 mcp 的 source code」「現在他們會把帳密放原始碼，但是 ai 讀得到，等於開放 ai 盜取帳密」「我們除了讓 ai 看不到原始碼，也需要提供正當方式讓作者不要把帳密放在裡面」。

條件〔user〕：
- 帳密是工具（MCP）這一方的，**使用者也不該知道**——所以不能叫使用者在環境變數面板填。
- 工具作者不是程式專業，「只需要知道怎麼寫 mcp，其他都不需要」——自己架後端服務這條路否決（而且「api 也是任何人可以打」）。
- 「不要犧牲功能，但要簡單」。

## 現況〔查證〕

| 事實 | 位置 |
|---|---|
| 工具 bundle 解開後是 `root:root 755`，掛在沙盒的 `/.tools/<name>`——沙盒的程序改不了，但**讀得到** | `sandbox-host/src/sandbox_host/tool_cache.py`（`_harden`、`staging.chmod(0o755)`） |
| AI 的 shell 指令和「平台啟動工具」呼叫的是**同一個** `sandbox.exec`，同一個身分 | `src/workspace_app/tooling/registry.py:600`（`_exec_tool`）；`src/workspace_app/agent/tools.py`（`exec_impl`） |
| 每個 item 一個固定的一般使用者（不是 root）；執行時先加入 item 的 cgroup、再 `setpriv` 降權 | `sandbox-host/src/sandbox_host/isolated_process.py:75`（`_setpriv_cgroup_argv`） |
| 回收沙盒用 `cgroup.kill`——砍 cgroup 裡**所有**程序，不看身分 | `isolated_process.py:145-149` |
| workspace 屬於 item 身分（`0700`），並且**已經用 ACL**（含預設 ACL）讓指定身分讀寫 | `isolated_process.py:66`（`_acl_argv`）、`:321`（`_provision`） |
| AI 的 shell 與所有工具共用**同一個 HOME**（每個沙盒自己的 `.home`，`0700`） | `sandbox-host/src/sandbox_host/local_process.py:1165` |
| 工具的啟動腳本把 `$HOME/.local/.../site-packages` 放在最前面——使用者 `pip install` 的套件就是這樣給內建 python 用的 | `src/workspace_app/tooling/prebuild.py:59-78` |
| 沙盒裡的 `python`／`python3`／`pip` 是連到 `/.tools/python-stack/launch` 的連結——**AI 直接執行的就是一個工具包** | `src/workspace_app/sandbox/local_process.py:163-171` |
| 工具要的環境變數：`env.json` 宣告名字，值由**使用者**在面板填；AI 的 shell 指令不帶這些變數 | `docs/tool-authoring.md` §7；`src/workspace_app/tooling/artifact.py:232` |
| 平台給作者的規則只有一句「不要把 secret 放進 bundle，走環境變數」——沒有給「使用者不該知道的帳密」放的地方 | `docs/tool-authoring.md`（「不要把 secret 放進 bundle」） |
| 工程師也能在自己電腦用 mcp-runner 跑同一份 artifact；bundle 會下載到他的電腦 | `docs/deployment.md` §15.8；`sandbox-host/src/sandbox_host/mcp_runner.py` |

合起來：AI 用 `exec` 就能 `cat /.tools/<工具>/…` 讀到原始碼（和寫在裡面的帳密）；工具執行中，AI 也讀得到它的環境變數（同一個身分讀得到 `/proc/<pid>/environ`）。

## 範圍

- **防的是平台 workspace 裡的 AI（和使用者）**〔user〕。
- **工程師自己電腦上的 agent 不在範圍**：bundle 在他的電腦上、他有那台電腦的最高權限，擋不住；要擋只能改成「程式碼不離開伺服器」的遠端 MCP，這次不做。
- **伺服器無法證明用戶端程式是原版**——同一個道理，另一題（小幫手安裝認證）另案處理，不在這份計劃。

## 現在 vs 改完之後

| | 現在 | 改完之後 |
|---|---|---|
| 工具用什麼身分執行 | 和 AI 同一個（item 的一般使用者） | 每個 item 另一個「工具身分」（一般使用者，不是 root，碰不到別的 item） |
| AI 能不能讀工具原始碼 | 能 | 不能〔user：「一樣不能讀原始碼」〕 |
| 帳密放哪 | 作者寫在原始碼 | 作者設在 GitLab 專案的 CI/CD Variables；打包時加密進 artifact；平台執行時才解開 |
| 帳密誰看得到 | 讀得到原始碼的人，包括 AI | 只有那支工具執行中的程序 |
| AI 能不能讀工具執行中的環境變數 | 能（同一個身分） | 不能（不同身分） |
| 工具把帳密印出來時 | 原樣回給 AI | 平台遮成 `***` |
| 工具出錯時 AI 看到什麼 | 完整 traceback（含原始碼行） | 拿掉原始碼行；`TOOL_DEBUG=名稱,名稱` 列到的工具不過濾 |
| 工具寫出來的檔案 | 屬於 item 身分 | 屬於工具身分；靠 ACL，使用者和 AI 照樣能改、能刪 |
| 工具在哪跑 | 平台與工程師本機都行 | 一樣；但用了加密帳密的工具本機解不開，mcp-runner 會明講原因 |

## 做法〔提案〕

### 1. 工具身分

- 每個 item 多一個工具身分，編號用和 item 身分**同一個算法**再位移一段（`uid_base + hash(item) % uid_range` 的那一套，另一段範圍），所以每個 pod 算出來都一樣、不需要協調。
- **沙盒介面加一個「以工具身分執行」的選項**。只有 `tooling/registry.py:_exec_tool`（平台啟動工具的唯一一條路——AI 的工具呼叫、WUI 的 `callTool`／`startTool`、view plugin 的沙盒端都走它）會設；AI 的 `exec` 工具沒有任何參數碰得到它。每一種沙盒後端都要認得這個選項（正式環境的 sandbox-host、本機模式、測試替身）。這是整件事唯一動到介面的地方。
- 啟動時沿用 `_setpriv_cgroup_argv`，只是換身分——所以工具**仍在 item 的 cgroup 裡**（資源限制、計費不變），回收沙盒時 `cgroup.kill` 一樣砍得到。
- 沒有 root 的本機開發模式做不到切換身分：照現在的方式跑，啟動時在 log 寫一行「這個部署的工具原始碼與環境變數沙盒讀得到」〔user：本機模式他用不到〕。

### 2. 哪些藏、哪些不藏

〔user 2026-11-16〕**線上的工具一律藏**，不逐一對清單。唯一的例外是 `python-stack`——藏了 AI 就跑不了 python，這是技術限制不是選項。

原則：**AI 會直接執行的不藏；由平台代為啟動的才藏。** 用「這個包有沒有命令」自動判斷（沒有命令的包是執行環境），不需要逐一登記。

| | 東西 | 理由 |
|---|---|---|
| 不藏 | `python-stack`（沙盒的 `python`／`pip`） | AI 直接執行；內容是公開套件，沒有帳密 |
| 藏 | 第三方工具（GitLab artifact） | 主要目標 |
| 藏 | 內建的命令型工具（示範設定裡是 `data-fetch`、`csv-column-summary`、`sci-plot`、`rca-tools`） | 平台代為啟動 |
| 藏 | view plugin 的沙盒端（`/.tools/<plugin>`） | 走同一條啟動路徑；分開處理就變成兩套規則 |
| 不受影響 | workspace 裡的檔案（使用者與 AI 自己的腳本、`.workflows/`、skill）、使用者 `pip install` 的套件、專案的 `.venv` | 本來就不在 `/.tools` |

藏的方式：那些包的目錄改成只有工具身分讀得到。（bundle 是整台主機共用的一份，而工具身分是每個 item 一個——所以「誰讀得到」不能直接寫成擁有者；做法在〈還沒決定的實作細節〉。）

### 3. 檔案與 HOME

- **workspace**：建立沙盒時，替工具身分多加一筆 ACL（含預設 ACL），和現有的 `_acl_argv` 同一個機制。工具寫的檔案，使用者與 AI 能改能刪；AI 寫的檔案，工具能讀能寫。（先前討論過「共用群組」，查到 workspace 已經在用 ACL 之後改成這個——不必動群組與預設權限。）
- **HOME**：工具身分有自己的 HOME（現在的 `.home` 是 item 身分專屬的 `0700`）。使用者 `pip install` 的套件（item 身分的 `~/.local`）工具**照樣讀得到、但只能讀**——這是 #393 的既有行為，不能弄丟。

### 4. 帳密

- 作者在 GitLab 專案的 **Settings → CI/CD → Variables** 設帳密（Masked、Hidden），在 `env.json` 標 `"secret": true`。只有這兩步，都是作者已經在用的東西。
- builder（作者的 CI 本來就在跑）打包時從 CI 環境讀到值，**用平台的公鑰加密**，密文跟著 artifact 發布。
- sandbox-host 執行那支工具時用私鑰解開，當成環境變數只給那支工具的程序。
- 結果：repo 與 artifact 裡只有密文；AI、使用者、能下載 artifact 的人、用 mcp-runner 的工程師都解不開。
- 本機 mcp-runner 沒有私鑰：遇到帶加密帳密的工具，明講「這支工具只能在 AI Workspace 使用」。（先前討論過讓作者宣告「在哪跑」——不做，解不開本身就是答案。）
- 沒標 `secret` 的變數照舊由使用者填。作者沒改的舊工具照樣能跑。

### 5. 回給 AI 的輸出

- 錯誤訊息拿掉 traceback 裡的原始碼行（留錯誤類型、訊息、檔名與行號）。
- **`TOOL_DEBUG=名稱,名稱`**〔user〕：列到的工具不過濾。讀的就是工具平常拿到的環境變數——不另外管它來自哪一層〔user：「那是其他層的事情不要越界」〕。
- 輸出裡出現解開的帳密原字串時遮成 `***`。
- 過濾與遮蔽做在工具輸出**進平台的那一個點**，所以聊天裡的工具卡片、執行紀錄、回給 AI 的文字是同一份。

## 上線當下會壞什麼

沙盒重開就好的（重開時會重跑「建立沙盒」）：上線當下還開著的舊沙盒沒有工具身分的 ACL 與 HOME，工具會失敗。

**重開也修不好的**（只列以前沒有、改完才有的）：

| # | 會壞什麼 | 處理 |
|---|---|---|
| 1 | 寫死路徑、用 `exec` 或 workflow 的 `run:` 步驟直接執行 `/.tools/<工具>/launch` 的地方——改完是「權限不足」。平台的 prompt、skill、workflow 與 WUI 文件都沒有教這個用法〔查證〕；使用者自己的腳本裡有沒有，查不到 | 上線前在 staging 搜；錯誤訊息要說「請用工具呼叫」 |
| 2 | AI 與工具原本透過同一個 HOME 共用的東西（AI 在 shell 登入某個 CLI、工具讀同一份設定）——HOME 分開後工具讀不到 | `pip install` 的套件已處理（第 3 節）；其他的不是工具的正式用法，寫進升級手冊 |
| 3 | 正式環境的 workspace 磁碟不接受第二筆 ACL——每次建立沙盒都失敗 | 上線前先在 staging 驗（現有的 ACL 已經在用，預期可行，但要量） |
| 4 | 工具自己把檔案建成「只有自己能讀」（`0600`）——使用者改不了那個檔 | 少見；需要時平台在工具結束後修正 |
| 5 | 工具出錯時 AI 看不到原始碼行，在平台上除錯變難 | 設計如此；`TOOL_DEBUG` 或在本機 mcp-runner 看 |
| 6 | 用了加密帳密的工具不能在工程師本機的 mcp-runner 跑 | 設計如此；runner 明講原因 |

先前擔心、查證後不成立的：「工具程序收不乾淨」與「工具逃出資源限制」——回收用 `cgroup.kill`、啟動沿用 `_setpriv_cgroup_argv`，兩者都看 cgroup 不看身分。仍要各有一條測試釘住「工具身分的程序在 item 的 cgroup 裡」。

## 這次不管的風險

user：「我們以後會加上 ai audit 在 build 時，所以有些東西不用現在管」——工具功能開太寬（AI 借工具使用帳密的權限）、作者發布惡意或有漏洞的新版、把帳密編碼後印出來（遮蔽只認得原字串）、例外訊息本身帶出程式片段。

仍要自己顧的：
- **能改工具 repo 或 CI 的人拿得到帳密**（在 CI 加一行印出變數）。repo 的寫入權等於帳密的讀取權——GitLab 權限管理的事，寫進作者指南。
- **平台的解密金鑰**：放在 k8s secret、限制誰讀得到；換金鑰的流程（換了之後各工具要重新打包）。

## 還沒決定的實作細節

- **bundle 怎麼做到「只有工具身分讀得到」**：bundle 是主機共用的一份（依 sha），工具身分是每個 item 一個。候選：一個共用的「工具群組」，bundle 設成 `root:工具群組 750`，每個 item 的工具身分啟動時帶上這個群組（`setpriv --groups`）；item 身分不帶（現在就是 `--clear-groups`）。影響範圍：

  | 會動到 | 影響 |
  |---|---|
  | `tool_cache.py` 解開 bundle 的權限 | `755` → `root:工具群組 750`；主機上已解開的舊 bundle 啟動時重設一次 |
  | 映像裡的 `builtin/` | 權限在打包映像時定——改 Dockerfile 或啟動時設 |
  | 啟動工具的 `setpriv` | 工具多帶一個群組；AI 的 `exec` 不變 |
  | **mcp-runner** | 和 sandbox-host 共用 `tool_cache`，runner 要維持 `755`（它降權後要進得去）——權限要能切換，加測試釘住 runner 沒壞 |
  | 群組編號 | 先寫死固定編號，不開設定鍵 |

  不受影響：workspace 的檔案（工具建立的檔案群組仍是 item 自己的）、`python-stack`（維持 `755`）、本機開發模式、AI 的 `exec`、資源限制與計費。

  新出現的副作用（以前沒有的才列）：權限改錯會讓 mcp-runner 進不了工具目錄（靠測試擋）；寫死的群組編號若撞到主機上既有的群組，那個群組的程序也讀得到 bundle（挑沒人用的編號）。
- 平台公鑰怎麼交到 builder 手上（烤進 builder 映像，或放在平台的一條公開路由）。
- 金鑰格式與加密方式（照成熟做法選，例如 age／libsodium sealed box；不自己發明）。
- 解密發生在 sandbox-host 還是 app 端、密文與明文各自經過哪些元件。
- 工具身分的編號範圍，以及和既有 `uid_base`／`uid_range` 設定的關係（會不會是一個新的設定鍵——是的話要記進升級手冊）。

## 上線前要先驗證（沒做完不動工）

1. 正式環境的 workspace 磁碟接受第二筆 ACL，且預設 ACL 對兩個身分互相建立的檔案都有效（含資料夾、含從備份還原回來的檔案）。
2. （取消——user：線上一律藏，不逐一對清單。）mcp-runner 在新的 bundle 權限下照常能跑。
3. staging 上搜尋寫死 `/.tools/` 路徑的 workflow 與腳本。
4. 作者的 GitLab CI 讀得到 CI/CD Variables 並交給 builder（受保護分支的變數只在受保護分支出現）。

## Phases（核准後才細分；編號連續）

- **P1** 沙盒介面的「以工具身分執行」選項＋sandbox-host 的工具身分（ACL、HOME、cgroup）；所有工具改走它；`python-stack` 不變。bundle 仍然讀得到——這一步只換身分，先確認什麼都沒壞。
- **P2** bundle 改成只有工具身分讀得到；`exec` 直接跑工具的錯誤訊息。
- **P3** 錯誤訊息過濾＋`TOOL_DEBUG`＋輸出遮蔽。
- **P4** `env.json` 的 `secret`、builder 加密、sandbox-host 解密注入；mcp-runner 的說明。
- **P5** 文件：`tool-authoring.md`（帳密的正當做法）、`deployment.md`（金鑰）、`migrations.md` 條目、staging 驗證清單。
