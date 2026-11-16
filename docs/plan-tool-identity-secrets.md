# 工具用自己的身分執行：AI 讀不到工具的原始碼，也拿不到工具的帳密

**狀態：方向已定（2026-11-16），尚未核准施工。** 〈問題陳述〉與〈已定的方向〉是 user 確認過的；〈做法〉是照這些決定寫的，施工前還要過〈上線前要先驗證〉。上一輪「所有工具都藏」的草案已被否定並刪除（在 git 歷史 `47aa2c76`）。

來源標記：**〔user〕** = user 決定；**〔查證〕** = 讀 `origin/master`（`1f0056c8`）程式碼確認的事實，附位置；**〔提案〕** = 我提的、user 還沒逐條確認的實作做法。

## 問題陳述〔user 確認「正確」，2026-11-16〕

### 問題

1. **工具作者把帳密寫在工具的原始碼裡。** 例如查機台資訊的工具，機台的帳密直接寫在程式裡。
2. **平台上的 AI 讀得到工具的原始碼。** 工具檔案放在沙盒的 `/.tools/`，權限是誰都能讀；AI 下一行指令就能把整份程式讀出來，帳密也在裡面。
3. **所以 AI 拿得到帳密，使用者也能叫 AI 把帳密拿出來。** 拿到帳密，就能繞過工具、直接對機台做工具沒有開放的事。
4. **作者沒有別的地方可以放這種帳密。** 平台目前只提供「使用者自己在環境變數面板填」這一個管道；但這組帳密是工具這一方的，不該讓使用者知道，也不該要使用者去填。所以作者只能寫進程式。

### 要達成的

- 平台上的 AI 讀不到工具的原始碼。
- 平台上的 AI 和使用者都拿不到工具的帳密。
- 作者有一個正當的地方放帳密，不必寫在程式裡。

### 限制

- 作者不是程式專業，只會寫 MCP 工具，不能要他們架服務或學新東西。
- 公司有 GitLab 可以用。
- 現有的功能不能少。
- 改完上線不能把現在能用的東西弄壞。
- 做法要簡單。

### 不在這次範圍

- 工程師在自己電腦上用 mcp-runner 跑工具：程式在他的電腦上，擋不住。

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

## 已定的方向〔user，2026-11-16 第二輪〕

**範圍改成：只藏第三方工具**（取代先前的「線上一律藏」）。內建工具、`python`（`python-stack`）、view plugin 完全不動。第三方與內建在 sandbox-host 本來就分開放（`tool_cache.py` 的 `ext/<sha>` 與 `builtin/`），不必另外登記。

第三方工具會變的兩個行為，user 都判定是**該改的，不是損失**：

| 會變的行為 | 為什麼必須變〔查證〕 | user 的判定 |
|---|---|---|
| 第三方工具只用自己打包時帶的套件，不再讀使用者在沙盒裡安裝的 | 現在的啟動腳本（`tooling/prebuild.py` 的 `_LAUNCH`）先讀使用者的安裝資料夾，也接受使用者設的 `PYTHONPATH`；那個資料夾 AI 寫得進去，放進去的程式會在工具裡執行、拿得到帳密。平台對 view plugin 已經這樣做（`_ISOLATED_LAUNCH`） | 改對了——MCP 本來就不該跟使用者的環境糾纏 |
| 不能在指令列或腳本裡直接執行 `/.tools/<第三方工具>/launch` | 檔案只有平台啟動工具的身分讀得到。平台的程式、文件、提示詞、skill 都沒有這種用法；而且直接執行時工具本來就拿不到環境變數面板的值 | 本來就該擋；錯誤訊息要寫清楚「只能透過工具呼叫使用」 |

使用者真正在用的功能不受影響：安裝套件後用 `python` 跑自己的程式（#581 要解決的事）照舊。

上一輪兩個「可能致命」的重新判斷〔查證〕：
- **正式環境的磁碟接不接受 ACL**：建立沙盒時本來就跑 `setfacl`，失敗會直接報錯（`sandbox-host/.../isolated_process.py:179`，`check=True`）；多一個身分是同一個指令多一個參數。前提是線上有開 per-item 身分隔離（看不到線上設定）。
- **硬碟額度**：用 `du` 算，不看檔案擁有者（`sandbox-host/.../local_process.py:1474`）。

**帳密的作者流程**〔user：「可以」〕：作者把帳密設在 GitLab 專案的 Settings → CI/CD → Variables，在 `env.json` 把那個變數標成機密，程式用 `os.environ[...]` 讀。加密與解密由平台做。

## 現在 vs 改完之後（只講第三方工具）

| | 現在 | 改完之後 |
|---|---|---|
| 用什麼身分執行 | 和 AI 同一個（item 的一般使用者） | 每個 item 另一個「工具身分」（一般使用者，不是 root，碰不到別的 item） |
| AI 能不能讀原始碼 | 能 | 不能 |
| 帳密放哪 | 作者寫在原始碼 | GitLab CI/CD Variables；安裝包裡只有加密過的內容 |
| 帳密誰拿得到 | 讀得到原始碼的人，包括 AI | 執行中的那支工具，以及能管理那個 GitLab 專案的人 |
| AI 能不能讀工具執行中的環境變數 | 能（同一個身分） | 不能（不同身分） |
| 用哪一份套件 | 先用使用者在沙盒裡裝的 | 只用自己打包時帶的 |
| 在指令列直接執行工具檔案 | 可以 | 拒絕，訊息說明「只能透過工具呼叫使用」 |
| 出錯時 AI 看到什麼 | 完整 traceback（含原始碼行） | 拿掉原始碼行；`TOOL_DEBUG=名稱,名稱` 列到的工具不過濾 |
| 工具把帳密印出來時 | 原樣回給 AI | 遮成 `***` |
| 工具寫出來的檔案 | 屬於 item 身分 | 屬於工具身分；靠 ACL，使用者和 AI 照樣能改、能刪 |

作者沒改的舊工具（帳密還寫在程式裡）照樣能跑，而且 AI 已經讀不到——作者可以慢慢搬。

## 做法〔提案〕

### 1. 工具身分

- 每個沙盒多一個工具身分，**從同一個 uid 池多借一個**（施工時查到：sandbox-host 是用池子配 uid，不是 app 端那種雜湊；線上的 199989／199992 正是預設池 100000–199999 的頂端）。池子本來就是 operator 留給沙盒的範圍，所以不需要新的設定鍵，也撞不到主機上的帳號。代價：每個沙盒佔兩個號碼，一台主機可同時開的沙盒上限從約十萬變約五萬。
- **工具群組** = 池子的第一個號碼（`uid_min`），池子改從 `uid_min + 1` 開始配——沒有任何沙盒 uid 會拿到它。
- app 端的三種後端（local／docker／mock）本來就不掛第三方工具（`SandboxSpec.tools` 註明忽略），所以只接受這個參數、不做事；換身分只在 sandbox-host。
- **沙盒介面加一個「以工具身分執行」的選項。** 只有 `tooling/registry.py:_exec_tool` 在啟動**第三方**工具時會設（AI 的工具呼叫、WUI 的 `callTool`／`startTool` 都走這一條）；AI 的 `exec` 工具沒有任何參數碰得到它。每一種沙盒後端都要認得這個選項。
- 啟動沿用 `_setpriv_cgroup_argv`，只換身分——工具仍在 item 的 cgroup 裡（資源限制、計費不變），回收沙盒時 `cgroup.kill` 一樣砍得到。
- 沒有 root 的本機開發模式做不到切換身分：照現在的方式跑，啟動時在 log 寫一行說明〔user：本機模式用不到〕。

### 2. 檔案

- **第三方工具的目錄（`ext/<sha>`）**：改成 `root:工具群組 750`；以工具身分啟動時帶上這個群組，AI 的 `exec` 不帶（現在就是 `--clear-groups`）。`builtin/` 不動。
- **mcp-runner** 和 sandbox-host 共用 `tool_cache`，它要維持現在的權限——權限做成可切換，並有一條測試釘住 runner 沒壞。
- **P2 施工時查到**：`{artifact: url}` 的 view plugin 也是從第三方倉庫掛載的，藏起來之後若還用沙盒身分跑就讀不到自己。所以「用哪個身分跑」改成**跟著掛載來源走**（`registry._mounted_from_the_third_party_store`：`third_party` 或名字在 `sandbox_spec.tools` 裡）——和 host 決定藏哪些的規則是同一件事。結果：artifact view plugin 照常能用，但 AI 讀不到它的檔案；`{bundle: …}` 的 view plugin 不變。
- 群組權限：bundle 改成 `root:工具群組`，群組拿到原本「其他人」的讀取／進入權限、其他人歸零（`tool_cache._GroupHardener`）——工具身分碰得到的正好是以前沙盒碰得到的。主機上升級前已解開的 bundle 在 sandbox-host 啟動時重設一次（`ToolCache.reharden_installed`）。
- AI 用 shell 碰第三方工具失敗時，結果開頭多一句說明：它只能透過工具呼叫使用（`agent/tools.py:_third_party_from_shell`；給模型的話照慣例用英文）。
- **workspace**：建立沙盒時替工具身分多加一筆 ACL（含預設 ACL），和現有的 `_acl_argv` 同一個指令。
- **HOME**：工具身分有自己的 HOME（AI 寫不進去）。

### 3. 工具的程序裡不能有 AI 寫得到的程式

- HOME 換成工具自己的之後，啟動腳本讀的「使用者安裝資料夾」就落在 AI 寫不進去的地方——不必重新打包既有的工具。
- 交給第三方工具的環境變數濾掉會改變「從哪裡載入程式」的名字（`registry._LOADER_NAMES`：`PYTHON*`、`LD_*`、`SANDBOX_*`、`BASH_FUNC_*`、`HOME`、`PATH`、`BASH_ENV`、`NODE_OPTIONS` 等，參照 sudo 的 `env_delete` 預設），並從啟動腳本還原用的名單（`SANDBOX_USER_ENV_KEYS`）一起拿掉——否則腳本會把它還原成空值。其餘使用者設的變數照舊交給工具。
- sandbox-host 對工具指令固定 `HOME`／`SANDBOX_HOME`＝`.tool-home`、`PYTHONNOUSERSITE=1`、`PYTHONSAFEPATH=1`，呼叫端傳來的同名變數蓋不過它們（`_TOOL_PINNED`）。

### 4. 帳密

- `env.json` 多一個欄位把變數標成機密；builder（作者的 CI 本來就在跑）打包時從 CI 環境讀到值，用平台的公鑰加密，密文跟著安裝包發布。
- sandbox-host 啟動那支工具時用私鑰解開，當成環境變數只給那支工具的程序。
- 工程師本機的 mcp-runner 沒有私鑰：機密變數由他自己在 shell 設（知道帳密的人才跑得起來）；沒設時 runner 明講缺哪個變數。
- 加密方式照成熟做法選（例如 libsodium sealed box／age），不自己發明。

### 5. 回給 AI 的輸出

- 第三方工具的錯誤訊息拿掉 traceback 裡的原始碼行（留錯誤類型、訊息、檔名與行號）。
- `TOOL_DEBUG=名稱,名稱`〔user〕：列到的工具不過濾。讀的就是工具平常拿到的環境變數——不另外管它來自哪一層。
- 輸出裡出現解開的帳密原字串時遮成 `***`。
- 做在工具輸出進平台的那一個點，所以工具卡片、執行紀錄、回給 AI 的文字是同一份。
- **P3 施工**：做在 sandbox-host 的 exec 輸出幫浦（`tool_output.ToolOutputFilter`，每個串流一個），所以即時串流和最後結果是同一份。逐行處理：一行沒結束前先留著，這樣跨兩次讀取的帳密也遮得到；一行太長（超過 64 KiB）時先遮再放出，只留最後「最長帳密長度減一」個位元組。traceback 只拿掉每個 `File …` 底下縮排的原始碼行與 `^^^` 標記，留下 frame 與例外那行；有拿掉時最後補一行提示可以用 `TOOL_DEBUG`。帳密遮蔽不受 `TOOL_DEBUG` 影響。

## 上線當下新出現的狀況（以前沒有的才列）

| # | 狀況 | 重開沙盒能解決嗎 | 處理 |
|---|---|---|---|
| 1 | 上線當下還開著的舊沙盒沒有工具身分的 ACL 與 HOME，第三方工具會失敗 | 能 | 啟動工具前發現缺就補上，或重開 |
| 2 | 第三方工具建成「只有自己能讀」的檔案（`0600`），使用者改不了 | 不能 | 少見；需要時平台在工具結束後修正 |
| 3 | 權限改錯會讓 mcp-runner 進不了工具目錄 | 不能 | 測試擋 |
| 4 | 一台主機可同時開的沙盒上限減半（每個沙盒佔兩個 uid） | 不能 | 預設池約十萬個號碼，減半後仍遠大於一台主機開得起的數量；寫進升級手冊 |
| 5 | 在平台上替第三方工具除錯，看不到原始碼行 | 不能（設計如此） | `TOOL_DEBUG` 或本機 mcp-runner |

## 這次不管的〔user〕

之後會在 build 時加 AI 稽核，所以這些現在不管：工具功能開太寬、作者發布惡意或有漏洞的新版、把帳密編碼後印出來、例外訊息本身帶出程式片段。

仍要自己顧、寫進文件的：能改工具 repo 或 CI 的人拿得到帳密（GitLab 權限）；平台解密金鑰的保管與更換（換了之後各工具要重新打包）。

## 上線前要先驗證（沒做完不動工）

1. ~~線上有開 per-item 身分隔離~~ **已確認（2026-11-16）**：user 在線上兩個 item 執行 `id -u`，得到 199989 與 199992——每個 item 身分不同。數字遠小於預設的起算點（一百萬），代表線上的 `uid_base`／`uid_range` 改過、可用範圍小。
2. staging 上：兩個身分互相建立的檔案（含資料夾、含從備份還原的）彼此都能讀寫。
3. 作者的 GitLab CI 讀得到 CI/CD Variables 並交給 builder（受保護的變數只在受保護分支出現）。
4. mcp-runner 在新的權限下照常能跑。

## 還沒決定的實作細節

- 平台公鑰怎麼交到 builder 手上；同一個安裝包要不要能給多個部署（staging／正式）解開。
- 解密發生在 sandbox-host 還是 app 端。
- ~~工具身分的編號範圍必須是設定鍵~~ **不需要**（P1 查證）：工具身分從既有的 uid 池借，工具群組用池子的第一個號碼。

## Phases（核准後才細分；編號連續）

- **P1** 沙盒介面的「以工具身分執行」選項＋sandbox-host 的工具身分（ACL、HOME、cgroup、環境變數過濾）；第三方工具改走它。目錄仍讀得到——先確認什麼都沒壞。
- **P2** `ext/<sha>` 改成只有工具群組讀得到；直接執行時的錯誤訊息；mcp-runner 的測試。
- **P3** 錯誤訊息過濾＋`TOOL_DEBUG`＋輸出遮蔽。
- **P4** `env.json` 的機密欄位、builder 加密、sandbox-host 解密注入。
- **P5** 文件：`tool-authoring.md`（帳密的正當做法）、`deployment.md`（金鑰）、`migrations.md` 條目、staging 驗證清單。
