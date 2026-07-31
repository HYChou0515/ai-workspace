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

## 6. 平台會怎麼跑你的工具

| | |
|---|---|
| **工作目錄** | 使用者的 workspace。路徑**一律相對於工作目錄**，`Path("notes/log.txt")` 就是使用者講的那個 |
| **可以寫的地方** | workspace 與暫存目錄。你的 bundle 是唯讀掛載的 |
| **`$HOME`** | 平台指定的、每個工作階段自己的目錄 |
| **`PATH`** | 收窄過。要 shell out 的東西，請自己確認它在 |
| **stdout** | 就是答案。診斷訊息走 stderr，失敗回非零 |
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
| 在 builder image 裡 build | 平台掛載前的閘門 |

## 8. 改東西的時候

**要改行為，請加一個新的 command。** 改名字、或加一個必填參數，會在你推上去的那一刻
對所有新開的工作階段生效，沒有版本閘。真的必須改，先跟平台團隊講一聲。

`version` 請照實往上加。它不參與任何判斷，但當有人回報「你的工具怪怪的」，那是唯一能對上話的東西。
