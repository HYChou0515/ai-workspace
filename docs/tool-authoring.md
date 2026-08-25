# 寫一支工具給這個平台（給外部工具作者）

!!! tip "先拿 `tool-starter/`"

    那個資料夾**本身就是一支能動的工具**：範例 command、可以直接跑的測試、CI 檔、
    以及一份 `CLAUDE.md`（讓 agent 先問清楚你要做什麼、再開始寫）。
    複製走、改名、換掉範例，就是你的工具。這一頁是它的背景說明。

你不需要我們 repo 的權限，也不用等我們發版。你在**自己的 GitLab** 寫工具、跑一個 CI job，
把產出的 artifact 網址給我們一次；之後你每推一版，**下一個開起來的 sandbox 就自動用新版**。

這頁只講你要做的事。平台那邊怎麼抓、怎麼掛，你不用知道。

---

## 1. 你的 repo 長什麼樣

```
my-wafer-tools/
  pyproject.toml          # 一個 [project.scripts] 進入點 + version（+ authors）
  uv.lock                 # 依賴釘死 —— 沒有它就沒有可重現的 build
  src/wafer_tools/
    cli.py                # 三段式契約（見下）
    commands/…
  .gitlab-ci.yml          # 我們提供的範本，貼上就好
```

`pyproject.toml` 只有兩件事是硬性的：

```toml
[project]
name = "wafer-tools"
version = "1.4.2"          # 必填。這是人在對話裡講的版本號
authors = [{name = "Wafer Team", email = "wafer@example.com"}]   # 選填，但請填

[project.scripts]
wafer-history = "wafer_tools.cli:main"   # 必須「剛好一個」
```

**`authors` 請填。** 它會進 manifest，然後出現在使用者的工具面板上：「這支工具是誰發布的、
現在跑的是哪一版」。工具出狀況時，那是使用者唯一找得到你的地方——不填的話那一欄會寫「未註明作者」。

它**不是**身分驗證。平台認的身分是我們簽發給你的憑證（§6），跟這個字串無關；也因為它不決定任何事，
你想怎麼寫就怎麼寫，寫錯也不會擋 build。代價是沒有人會替你檢查，所以 `build-tool` 會在 CI log
把它印出來（`published wafer-history 1.4.2 by Wafer Team <wafer@example.com>`）——**發版後看一眼那一行**，
沒印出名字就是沒填成功。

**為什麼只能一個 script**：bundle 的啟動器就是去執行這個進入點。兩個會讓「AI 剛才到底跑了哪一個」
變得沒有答案，零個則沒東西可跑。一個 package 可以有**很多 command**——那是下一節的事，不是靠多個
script 達成的。

**不要設 `[tool.uv] package = false`。** 有些團隊把「要部署的東西不是可散布的套件」當成內規，
但你的 repo 不是那種東西：uv 會把設了這個的專案當成 virtual project，**完全不建套件**，於是
`[project.scripts]` 那支執行檔不存在，而 bundle 的啟動器要執行的正是它。build 會停在

```
FileNotFoundError: [Errno 2] No such file or directory: '.../.venv/bin/wafer-history'
```

會失敗總比發出一個跑不起來的 bundle 好，但那串訊息指的是路徑而不是原因，所以先寫在這裡。

## 2. 三段式契約

你的 CLI 要能回答三種呼叫。前兩種是**給機器看的自我描述**，第三種才是幹活：

| 呼叫 | 要印出 |
|---|---|
| `wafer-history` | `[{"name": ..., "description": ...}, …]` —— 我有哪些 command |
| `wafer-history trend` | `{"name":…, "description":…, "params_json_schema":…}` —— 這個 command 吃什麼參數 |
| `wafer-history trend '<json>'` | 真的執行，結果印到 stdout |

**你不用手寫 JSON schema。** 用 pydantic 定義參數，`Args.model_json_schema()` 就是那個 schema；
build 的時候我們會跑你的 CLI 把它抓下來凍進 artifact。

（完整範例與 decorator 版的寫法，見
[擴充平台的 Tool 一節](extending-the-platform.md)——第一方工具用的是同一套契約。）

## 3. `.gitlab-ci.yml`

複製 [`tool-builder/gitlab-ci.example.yml`](https://github.com/HYChou0515/ai-workspace/blob/master/tool-builder/gitlab-ci.example.yml)：

```yaml
build-tool:
  image: registry.example/ai-workspace/tool-builder:2026.07
  script:
    - build-tool /src dist
  artifacts:
    paths: [dist/]
    expire_in: never          # 見下面的警告
```

產出兩個檔：

```
dist/tool.tar.gz         # 會跑的整包（你的程式 + 依賴 + 一顆 python）
dist/tool.manifest.json  # 平台讀的描述檔
```

!!! warning "`expire_in: never` 不是可選的"

    GitLab 的 CI artifact **預設會過期**（通常 30 天）。過期之後那個網址就 404，
    而已經抓過的機器還能靠快取撐著，**新開的機器會拿不到你的工具**。
    這種壞法最難查，因為「昨天還好好的」。

!!! danger "一定要在 builder image 裡 build"

    bundle 帶著自己的 python 和原生套件（numpy 那類），**只能在它被 build 出來的那個底層上跑**。
    在別的 image build 出來的東西，平台會**當場拒絕**——這比讓它在執行時 segfault 好，
    因為那種錯會發生在使用者面前，而且看不出跟你的 build 有關。

## 4. 不推 CI 也能先自己測

```sh
docker run --rm -v "$PWD:/src" -v "$PWD/dist:/dist" \
    registry.example/ai-workspace/tool-builder:2026.07 build-tool /src /dist

docker run --rm -v "$PWD/dist:/dist" \
    registry.example/ai-workspace/tool-builder:2026.07 smoke /dist
```

`smoke` 會把 bundle 解開、**在平台真正執行工具的那個底層裡**跑一遍三段式契約。
所以「我這邊會動」跟「平台上會動」是同一件事，不是碰運氣。

## 4b. 在真的 sandbox 裡跑（不用 push）

`smoke` 只確認你的工具**會自我介紹**（列出 command、吐 schema）。它不會帶著參數真的執行，
也不會重現平台實際給你的環境。要驗那件事，在自己機器上跑一個**真的** sandbox host：

```sh
export SANDBOX_HOST_IMAGE=<平台團隊給你的 image>
export TOOL_BUILDER_ID=<跟部署一致的值>
docker compose -f compose.tool-dev.yaml up -d
```

然後把平台的 `sandbox.kind` 設成 `http`、`base_url` 指到 `http://127.0.0.1:8000`，
把你的工具掛上去跑。**改一行、重跑、看結果，不用 commit、不用 push、不用等 CI。**

!!! warning "為什麼一定要 `privileged: true`"

    沒有它，核心會拒絕建立 jail，而 host **不會報錯**——它會安靜地退回沒有 jail 的模式，
    那裡的 `/.tools` 是 symlink 而不是唯讀掛載。於是「往自己旁邊寫檔案」的工具**在這裡會過、
    上線會壞**，正好是這個環境存在的理由。（實測：預設 docker 與
    `--security-opt seccomp=unconfined` 都不夠，要 `--privileged`。）

**它重現什麼**：command 怎麼被呼叫、cwd、`HOME`、`PATH`、bundle 唯讀、降權 uid、
時間上限、輸出上限——也就是 §6、§7 那兩張表裡的東西。

**它不重現什麼**：你的**網路位置**。你的機器不在正式環境的 nginx 後面，所以那裡自動加上的
header 這裡沒有，某些端點你這邊可能根本連不到。**會連外的工具，第一次遇到真實情況仍然是在正式環境**——
這條路關掉的是另外那一大半（環境與呼叫方式），不是全部。

## 4c. 失敗要回哪個 exit code（**契約**）

平台用 exit code 決定**告訴模型下一步怎麼走**：號碼是指引，訊息是細節。

| 你回 | 意思 | 模型會被告知 |
|---|---|---|
| `0` | 成功 | stdout 就是答案 |
| `2` | **再叫一次可能會成功**——參數可修、逾時、上游剛好不通 | 「可以再試一次；訊息若指出參數有問題，先改參數」 |
| `3` | **要有人先做一件事**——缺憑證、缺權限 | 「照原樣再叫也一樣會失敗，請告訴使用者要做什麼」。訊息請**指名**是哪個變數 |
| `1` | 其他失敗 | 照你的訊息回報 |

**`2` 是給模型的許可，不是平台自動重跑**——工具可能有副作用，平台不會替你重來。
`tool-starter/src/my_tool/common.py` 提供 `Retryable` / `NeedsAction` / `ToolError`，raise 就好。

平台自己也會產生幾個 code，你不用宣告，看到時知道意思即可：

| | |
|---|---|
| `124` | 逾時（總時限或閒置） |
| `-9` | 被沙箱砍掉——幾乎都是記憶體上限 |
| `-11` | segfault——通常代表 bundle 是為別的環境 build 的 |
| `126` / `127` | launcher 沒能啟動——bundle 壞了或沒掛上 |

## 5. 交給我們的，就一串網址

```
https://gitlab.example/api/v4/projects/<id>/jobs/artifacts/<ref>/raw/dist/tool.manifest.json?job=build-tool
```

這是 GitLab 的「最新 artifact」端點——**你推一版，它就指到新的**，我們不用改任何東西。

---

## 5b. 同一份 artifact，工程師也能用

你發布的那兩個檔案，同時就是別人可以透過 MCP 使用的東西——**CI 不必多跑一個 job，你也不必
發自己的 container image**。平台團隊發一顆 runner image，工程師拿它加上你的 artifact 網址，
就能用自己的 agent 呼叫你的工具;轉接器由 builder 注入 bundle，你不用寫任何程式。

設定範例見 `tool-starter/README.md` §5c。

## 6. 必須通過的檢查

這些不是建議，是有程式在擋的：

| | 會怎樣 |
|---|---|
| 三段式契約不完整 | `build-tool` 直接失敗 |
| 沒有 `uv.lock` | build 失敗——沒有它 bundle 不可重現 |
| **smoke 沒過** | **build 失敗，而且不留下任何 artifact**（免得 CI 把壞的傳上去） |
| 不是在 builder image 裡 build 的 | 平台拒絕掛載 |
| `[project.scripts]` 不是剛好一個 / 沒有 `version` | build 失敗 |
| 設了 `[tool.uv] package = false` | build 失敗——沒有進入點可執行（見 §1） |
| **bundle 壓縮後超過 150MB** | build 失敗，並列出最重的幾樣；平台上架時也擋 |
| **沒有平台憑證** | 平台拒絕執行——`tool-certificate.token` 是上架的前提，不只是體積的例外 |
| manifest 裡的名字跟我們登記的不一樣 | 平台拒絕——代表這個網址指到了別支工具 |

還有兩件**不是拒絕、但一定會發生**的事，不知道就會出事：

- **你的輸出會被截斷。** 每個工具的輸出都有上限（AI 的上下文是有限的）。要回大量資料，
  請寫成檔案再回一個路徑，不要整包往 stdout 倒。
- **執行有時間上限。** 預設整體 60 秒、閒置 60 秒（沒有輸出就算閒置）。長時間的工作要嘛
  切小，要嘛持續印進度。

### 體積上限與例外憑證

量的是**壓縮後**的 `tool.tar.gz`，也就是每台 host 實際下載的東西。空模板本身就約 40MB
（bundle 自帶的 python 直譯器），剩下的額度給你的相依。

只有測試用得到的套件請放 `[dependency-groups] dev`——build 會略過它們（`uv sync
--no-dev`）；寫在 `[project.dependencies]` 的一律打包。

真的需要更大的額度：把工具寄給平台團隊 review，通過後會收到一行憑證，存成 repo 根目錄的
`tool-certificate.token` 並提交。憑證綁定**工具名字**、寫明**放寬到多少**與**到哪天為止**
（或 `never`），並且跟著 manifest 一起發布，所以平台驗的和你 build 時用的是同一張。

憑證只在超過 150MB 時起作用，所以工具瘦下來之後，憑證過期不會擋到發版。

## 7. 你自己要顧好的部分

- **description 要好好寫。** AI 會不會用你的工具、參數填不填得對，幾乎完全取決於
  command 和參數的 description——它們會被原封不動放進 AI 的提示裡。名字取得再好，
  description 寫「do stuff」就等於沒有。

- **改 command 名、或加一個必填參數，是 breaking change，而且立刻生效。**
  沒有版本閘：你推上去，下一個開起來的 sandbox 就是新的。要改請發一個新的 command 名，
  或先跟我們說一聲。

- **不要把 secret 放進 bundle。** 同一台機器上所有 sandbox 共用同一份 bundle。
  需要金鑰的話走環境變數（見
  [擴充平台的 Tool 一節](extending-the-platform.md)）。

- **`source` 欄位是給人看的溯源。** 平台不會回頭 clone 你的 repo，也不會因為
  「它來自 git」就更信任它。

---

## 8. manifest 裡有什麼

你不用手寫，`build-tool` 會產生。但看得懂它有助於查問題：

| 欄位 | 意思 |
|---|---|
| `format_version` | manifest 的格式版本。平台不認得就拒絕 |
| `name` / `version` | 你的工具叫什麼、哪一版（`version` 純粹給人看） |
| `author` | 誰發布的，取自你的 `[project].authors`。純顯示，跟信任無關；沒填就沒有這個欄位 |
| `commands` | 每個 command 的 name / description / JSON schema |
| `builder` | **你是在哪個 builder image 裡 build 的**。平台拿它比對 ABI |
| `python` / `arch` | 直譯器版本與 CPU 架構 |
| `bundle.sha256` / `bundle.size` | bundle 的指紋與大小 |
| `source` | git 網址與 commit，純溯源 |

## 9. 上架 / 更新 / 退回，誰做什麼

| 場合 | 你 | 我們 |
|---|---|---|
| **第一次上架** | 寫工具 → CI → 給網址 | 驗過 → 登記 → 發版 |
| **你發新版** | push 就好 | **什麼都不用做** |
| **要退回舊版** | （可選）自己回退 | 把登記的網址指到特定那次 build |
| **下架** | — | 從設定拿掉 |
