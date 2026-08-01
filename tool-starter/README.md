# 寫一支工具

這個資料夾就是一個**能動的工具**。把它複製走、改名、換掉裡面那個範例 command，
就是你的工具了。

你不需要平台的原始碼，也不需要平台團隊幫你發版。你在自己的 repo 開發、在自己的 CI build，
把產出的網址給他們一次；**之後你每推一版，下一個開起來的工作階段就自動用新版。**

如果你用 Claude Code 之類的 agent 協助開發，`CLAUDE.md` 已經寫好了——它會先問你想做什麼，
再開始寫。

---

## 1. 先跑起來（5 分鐘）

```sh
uv sync
uv run pytest            # 3 個測試會過

uv run my-tool           # → [{"name":"count","description":…}]      有哪些 command
uv run my-tool count     # → {"name","description","params_json_schema"}   吃什麼參數

# 真的做事。cd 進去、用絕對路徑直接呼叫 venv 裡的執行檔 —— 因為工作目錄就是
# 「使用者的 workspace」，而你的工具在別的地方。（`uv run` 會把工作目錄換成專案目錄。）
mkdir -p /tmp/ws && printf 'one two\n\nthree\n' > /tmp/ws/a.txt
cd /tmp/ws && "$OLDPWD/.venv/bin/my-tool" count '{"path":"a.txt"}'
# → {"path": "a.txt", "lines": 3, "words": 3}
```

那三個呼叫就是**平台跟你的工具說話的全部方式**：問你有哪些 command、問某個 command 吃什麼
參數、然後帶著參數叫它做事。沒有別的介面。

參數不合會怎樣也順便看一下——訊息走 stderr、exit code 非零，stdout 保持乾淨：

```sh
uv run my-tool count '{"nope":1}'   # → bad arguments for count: … ; exit 1
```

## 2. 換成你的東西

1. `pyproject.toml`：改 `name`、`[project.scripts]` 的那一行（**只能有一行**）、`dependencies`。
2. 把 `src/my_tool/` 改名成你的套件名，改掉 import。
3. 在 `src/<你的套件>/commands/` 下寫你的 command，在 `commands/__init__.py` 加一行。
4. 改 `tests/`。

`cli.py` 幾乎不用動——它就是那個三段式契約。

## 3. 一個 command 長什麼樣

每個 command 就是三樣東西：

| | 作用 |
|---|---|
| `DESCRIPTION` | **模型看這一句決定要不要叫你**。請寫成一個真正的句子 |
| `Args`（pydantic） | 同時產生模型看到的 JSON schema **和**執行期驗證，只有一份真相 |
| `run(args)` | 幹活，回傳字串 |

**兩種寫法都可以，`cli.py` 一視同仁**——挑讀起來順的那種：

| 檔案 | 寫法 |
|---|---|
| `commands/count.py` | 把三樣東西攤開寫，在 `commands/__init__.py` 列一行 |
| `commands/head.py` | 一個 `@command(...)` 裝飾的函式，import 時自己註冊 |

那個裝飾器在 `src/my_tool/common.py`——**它是你的檔案**，在你的 repo 裡，可以改也可以刪。
它刻意不是從平台 import 來的：這樣你只裝自己的相依就能跑 `pytest`，平台改版也不會動到你。

## 4. 發版

複製 `.gitlab-ci.yml` 到你 repo 根目錄，把 image 換成平台團隊給你的位址。

```yaml
build-tool:
  image: <平台團隊給你的 builder image>
  script:
    - build-tool "$CI_PROJECT_DIR" dist
  artifacts:
    paths: [dist/]
    expire_in: never
```

產出兩個檔：`dist/tool.tar.gz`（會跑的整包）與 `dist/tool.manifest.json`（描述檔）。
把那個 manifest 的網址給平台團隊，**一次就好**。

!!! warning "`expire_in: never` 不是可選的"

    GitLab 的 artifact 預設約 30 天過期。過期後那個網址就 404，
    **已經抓過的機器還能用，新開的機器拿不到**——症狀是「昨天還好好的」，最難查。

!!! danger "一定要在 builder image 裡 build"

    bundle 帶著自己的 python 和原生套件，只能在它被 build 出來的那個底層上跑。
    用別的 image build 出來的，平台會**當場拒絕**——這比讓它在使用者面前 segfault 好。

## 5. 在真的環境裡試（不用 push）

`build-tool` 會順便跑一次 smoke，但那只確認你的工具**會自我介紹**，不會驗證它在平台的環境裡
真的做得了事。要驗那件事，在自己機器上跑一個**真的** sandbox：

```sh
export SANDBOX_HOST_IMAGE=<平台團隊給你的 image>
export TOOL_BUILDER_ID=<平台團隊給你的值>
docker compose -f compose.tool-dev.yaml up -d
```

**改一行、重跑、看結果——不用 commit、不用 push、不用等 CI。**

**它重現**：command 怎麼被呼叫、工作目錄、`HOME`、`PATH`、bundle 唯讀、降權的使用者、
時間上限、輸出上限。也就是下面第 6 節那些會咬人的東西。

**它的邊界**：你的**網路位置**。你的機器不在正式環境的反向代理後面，所以那裡自動加上的
header 這裡沒有，某些端點你可能根本連不到。**會連外的工具，第一次遇到真實情況仍然是在正式環境。**

## 5b. 失敗要回哪個 exit code（**契約**）

平台用 exit code 決定**告訴模型下一步怎麼走**，所以號碼是指引、訊息是細節。
`common.py` 裡有對應的例外，raise 就好：

| 回什麼 | 意思 | 模型會被告知 |
|---|---|---|
| `0` | 成功 | 你印在 stdout 的東西就是答案 |
| `raise Retryable(...)` → `2` | **再叫一次可能會成功**（參數可修、逾時、上游剛好不通） | 「可以再試一次；訊息若指出參數有問題，先改參數」 |
| `raise NeedsAction(...)` → `3` | **要有人先做一件事**（缺憑證、缺權限） | 「照原樣再叫也一樣會失敗，請告訴使用者要做什麼」——**請在訊息裡指名是哪個變數／哪個權限** |
| `raise ToolError(...)` → `1` | 其他失敗 | 就照你的訊息回報 |

**`2` 是給模型的許可，不是平台自動重跑**——你的工具可能有副作用，平台不會替你重來。

平台自己也會產生一些 code，你不用管，但看到時可以知道意思：

| | |
|---|---|
| `124` | 逾時（總計或閒置） |
| `-9` | 被沙箱砍掉——幾乎都是記憶體上限 |
| `-11` | crash（segfault）——通常代表 bundle 是為別的環境 build 的 |
| `126` / `127` | 你的 launcher 沒能啟動——bundle 壞了或沒掛上 |

## 5c. 讓別人也能用（MCP）

你發布的那兩個檔案，同時就是別人可以用的東西——**你不用多做任何事，CI 也不必多跑一個
job**。平台團隊發一顆 **runner image**，任何有 docker 的工程師拿它加上你的 artifact 網址，
就能用自己的 agent（Claude Code／opencode／codex）呼叫你的工具。

你不用為此寫任何程式:三段式契約本來就等於 MCP 需要的東西，轉接器由 builder 注入到 bundle 裡。

使用者那邊不用手動設定：平台團隊會發一個 skill 給他們，他們把**你的 repo 網址**丟給自己的
agent，agent 就會把設定寫好。所以你要給人的東西就是 repo 網址，不是一串 docker 指令。

底下這段是 agent 會寫出來的內容，列出來讓你知道發生了什麼事：

```json
{
  "mcpServers": {
    "my-tool": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "mcp-tools:/cache",
        "-v", "${PWD}:/work",
        "-e", "TOOL_ARTIFACT_TOKEN",
        "<平台團隊給的 runner image>",
        "my-tool",
        "https://gitlab.example/.../tool.manifest.json"
      ]
    }
  }
}
```

**設定裡沒有任何跟他的機器有關的東西**——同一份可以直接發給所有人。runner 啟動時會自己
降權成 `/work` 目錄的擁有者,所以工具產出的檔案就是他的,不是 root 的。(要寫成
`--user "$(id -u):$(id -g)"` 是行不通的:MCP 設定最多展開環境變數,不會做命令代換,
那等於每個人都要手改一次自己的 uid。)

三個掛載各有各的必要:

- `-v ${PWD}:/work` —— 你的工具把路徑當成相對於工作目錄（平台上就是使用者的 workspace）。
  **這個對「會寫檔的工具」特別重要**:沒掛載時讀檔會大聲失敗（檔案不存在），但**寫檔會
  「成功」然後隨容器一起消失**——你的工具回報「已寫入 report.csv」,他的磁碟上卻什麼都沒有。
  runner 啟動時會在 stderr 提醒這件事,但掛上去才是解法。
- `-v mcp-tools:/cache` —— **可選**。掛了，bundle 依 sha 存在這裡，第一次抓、之後命中;
  不掛，每次啟動都重抓一次（會慢，但不留任何東西在機器上）。
  要掛就用 **named volume**（就是這樣寫），不要換成主機目錄:容器以 root 執行，bind mount
  會讓快取變成 root 所有，之後你自己刪不掉。named volume 用 `docker volume rm` 清即可。
- `-e TOOL_ARTIFACT_TOKEN` —— 讀 artifact 用的 token（私有 GitLab 才需要）。

**工程師會自動吃到你的新版**:runner 每次啟動都會看一次 manifest，sha 變了就抓新的。
和平台「下一個 sandbox 就是新版」是同一個性質。

所以**掛快取不會讓他們用到舊版**——每次啟動都還是會問一次 manifest，快取只是省下「已經有的
位元組再抓一遍」。掛與不掛的差別是磁碟換頻寬，不是新舊。

而且它拿到的 bundle 和平台拿到的是**同一份、經過同樣檢查的位元組**——同一段 resolve、
同樣的 builder 閘門、同樣的 sha 驗證。

!!! note "這條路證明的是邏輯，不是環境"

    工程師的 agent 跑起來時**不是 sandbox**:沒有輸出上限、沒有逾時、沒有平台注入的環境變數。
    當成「多一個使用途徑」很好，當成「平台上會過的證明」則不成立。

## 6. 平台會怎麼跑你的工具

| | |
|---|---|
| **工作目錄** | 使用者的 workspace。路徑**一律相對於工作目錄**，`Path("notes/log.txt")` 就是使用者講的那個 |
| **可以寫的地方** | workspace 與暫存目錄。你的 bundle 是唯讀掛載的 |
| **`$HOME`** | 平台指定的、每個工作階段自己的目錄 |
| **`PATH`** | 收窄過。要 shell out 的東西，請自己確認它在 |
| **stdout** | 就是答案。診斷訊息走 stderr，失敗照 §5b 的 exit code |
| **輸出** | 有上限。資料量大請寫成檔案、回傳路徑 |
| **時間** | 總計 60 秒，且**閒置 60 秒**也算逾時。長工作請持續印進度 |
| **身分** | 降權的使用者 |
| **秘密** | 走環境變數。同一台機器上所有工作階段共用同一份 bundle |

## 7. 必須通過的檢查

這幾條**有程式在擋**，不是建議：

| 必須 | 由誰擋 |
|---|---|
| 三段式契約完整 | `build-tool` |
| `uv.lock` 已提交 | `build-tool` |
| **smoke 通過** | `build-tool`——沒過就**不留下任何 artifact**，CI 想傳也沒得傳 |
| `[project.scripts]` 剛好一個、且有 `version` | `build-tool` |
| **bundle 壓縮後 ≦ 150MB** | `build-tool`，以及平台上架時的閘門（見 7b） |
| 在 builder image 裡 build | 平台掛載前的閘門 |

## 7b. 體積上限：150MB

`build-tool` 量的是**壓縮後**的 `tool.tar.gz`——那是每一台機器實際要下載的東西，也是
manifest 裡本來就記著的數字。超過就讓 build 紅，並且列出 bundle 裡最重的幾樣，你不用
自己猜。

空模板本身大約 40MB，幾乎全是 bundle 自帶的 python 直譯器。剩下的額度是給你的相依用的。

最常把額度吃掉的兩種：

- **只有測試用得到的套件。** 放進 `[dependency-groups] dev`，build 會自動略過
  （`uv sync --no-dev`）。寫在 `[project.dependencies]` 裡的一律會被打包進去。
- **為了一張圖帶進整套繪圖庫、為了讀一個欄位帶進整套資料科學堆疊。** 先確認執行時
  真的用得到。

### 真的需要更大的額度

把工具寄給平台團隊 review。通過的話你會收到**一行憑證**，存成 repo 根目錄的
`tool-size-grant.token` 並提交：

```
$ cat tool-size-grant.token
eyJleHBpcmVzIjoiMjAyNi0wOS0wMSIsIm1heF9ieXRlcyI6MzE0NTcyODAwLCJ0b29sIjoi….GkY1srqM…
```

它會跟著 manifest 一起發布，所以平台驗的和你 build 時用的是同一張。

憑證上寫著三件事：**哪一支工具**、**放寬到多少**、**到哪一天為止**（或 `never`）。
它綁定工具名字，所以別人的憑證對你沒有作用，你的對別人也一樣。

**憑證只在你超過 150MB 時才起作用。** 工具之後瘦下來的話，就算憑證過期也不會擋到你發版。


## 8. 改東西的時候

**要改行為，請加一個新的 command。** 改名字、或加一個必填參數，會在你推上去的那一刻
對所有新開的工作階段生效，沒有版本閘。真的必須改，先跟平台團隊講一聲。

`version` 請照實往上加。它不參與任何判斷，但當有人回報「你的工具怪怪的」，那是唯一能對上話的東西。
