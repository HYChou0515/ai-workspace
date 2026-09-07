# 擴充平台：Tools / Skills / Workflows

平台有三個讓 agent 變強的擴充面——**tool**（agent 能呼叫的動作）、**skill**（教
agent「某類任務怎麼做」的方法論）、**workflow**（把多個 step 串成可重跑的自動化）。
每個擴充面都有兩種作者：

- **dev 自建**——把原始碼／資料 **commit 進 repo**。它會出貨給那個 app／profile 的**所有人**,
  是 trusted code，走完整的 prebuild／重啟／CI／100% coverage gate。
- **user 自建**——在**執行期跟 AI 一起共創**，存成單一 workspace 裡的 **FileStore 資料**,
  live 讀取、可下載／匯入、可由 dev **升格**成內建。因為要在受信任的 API 邊界內保持安全,
  它是**受限**的。

Tool 這一面還多一條路（#674）:**dev 不一定要是我們**。外部工具作者可以在自己的 repo 寫工具、
在自己的 CI 用我們提供的 builder image build,把 artifact 的網址交給我們——不需要我們 repo 的
權限,也不用等我們發版。細節見 [寫一支工具（外部作者）](tool-authoring.md)。

第四個擴充面是 **view kind**（#698）。它跟上面三個不同——不是讓 agent 變強,而是讓**畫面**變多:
一種新的資料呈現方式,由 workspace 裡的 `*.ai.yaml` 檔案指名使用。它同樣有「dev 可以不是我們」
的性質,但走的是另一條路:程式碼放進**我們 repo** 的 `web/src/ext/`,跟平台一起編譯。細節見
[寫一個 View Kind（維運方）](view-kind-authoring.md)。

這篇是把上面幾個面向 × 兩種作者排成同一張表的**總覽**;每個面向的細節文件在各段末尾連出去。

## 一眼看懂：誰能建什麼

| 擴充面 | dev 自建 | user 自建（執行期 + AI 共創） |
|---|---|---|
| **Tool** | ✅ Python tool-package——**vendor 進 repo**（`sample-tools/`）**或外部作者自己的 repo + CI**（#674） | ❌ **無**——安全考量,見下 |
| **Skill** | ✅ `sample-skills/` + `SHARED_SKILLS` 註冊 | ✅ `author-skill` + `save_skill` → `.skill/`（#298） |
| **Workflow** | ✅ Python `run.py`（圖靈完備） | ✅ `workflow.json` **降階 DSL**（#323）——**最難的一塊** |
| **View Kind** | ✅ React 元件 + `web/src/ext/` 一行註冊（#698）——**dev 可以是維運方** | ❌ **無**——會執行任意前端程式碼 |
| **WUI** | ✅ 就是一個 workspace 資料夾——沒有 dev 專屬路徑 | ✅ **和 AI 共創**——`view: wui` + `index.html`（見 [`wui.md`](wui.md)） |

三個 user 自建路徑刻意共用同一套模型（照搬 #298 的 skill 流程）:**跟 AI 共創 → 存進
workspace 的點開頭資料夾 → 側邊面板列出／下載／匯入 → dev 把它 commit 進 profile 就升格成
內建**。差別只在**執行風險**:skill 是被動 markdown（零風險,放手讓使用者寫）;workflow
會**執行**,所以 user 端被降階成一個受控的 JSON DSL;tool 需要跑任意 Python + 持有 credential,
所以**沒有** user 自建路徑（但 tool 的「dev」可以是**外部作者**——那不是執行期共創,是另一個團隊的
deploy-time 動作,見 §Tool）。

---

## Tool（dev 自建;dev 可以不是我們）

一個 tool 是 agent 能呼叫的**動作**。實作是一個**自成一格的 Python package**,跑在 sandbox
裡(不是 host app 進程),透過一個固定的 argv 契約被呼叫。

### 檔案佈局

```
sample-tools/<name>/
  pyproject.toml            # package 定義 + 依賴 + ruff TID252（禁相對 import）
  uv.lock                   # 凍結依賴（prebuild 用 --frozen，可重現）
  env.json                  # 選填：這個 package 需要哪些環境變數（#750，見下）
  src/<pkg>/
    cli.py                  # 三段 dispatcher（entry point）— iterate COMMANDS
    core.py                 # 共用邏輯
    commands/               # 多 command package 才需要
      __init__.py           # COMMANDS dict ←「這個 package 有哪些 command」就在這
      summarise.py          # 一個 command = Args + DESCRIPTION + run()
      plot.py
  tests/
```

現有範例:`sample-tools/{data-fetch, csv-column-summary, sci-plot, rca-tools}`,外加
`python-stack`(一個沒有 command 的 venv carrier,workspace 內建 `python` 就靠它)。

### 三段 launcher 契約

每個 package 的 launcher 服從三段 argv 契約(backend → sandbox 零侵入,只傳三個字串):

```bash
$ ./launch                          # 零參 → 列出所有 command（JSON array）
[ {"name": "summarise", "description": "..."}, {"name": "plot", "description": "..."} ]

$ ./launch summarise                # 一參 → 該 command 的 metadata + JSON schema
{ "name": "summarise", "description": "...", "params_json_schema": { ... } }

$ ./launch summarise '{"csv":"x.csv"}'   # 兩參 → 執行；stdout / stderr / exit_code 回傳
```

作者寫一個 command 只要三樣東西——我們**不強制 decorator 或 framework**:

```python
# sample-tools/csv-column-summary/src/csv_column_summary/commands/summarise.py
from pydantic import BaseModel, Field

class Args(BaseModel):                              # 1. LLM 看到的參數 schema（自我描述）
    csv: str = Field(description="Path to the CSV file in the workspace.")

DESCRIPTION = "Summarise each column of a CSV ..."  # 2. LLM 看到的一行說明

def run(args: Args) -> None:                        # 3. 拿驗證過的 args 執行
    ...
    print(json.dumps({...}))                        # stdout = 給 agent 的 JSON；stderr = 進度
```

`cli.py` 把 command 湊成一個 `COMMANDS` dict 再自寫 `main()`(範例見
`data-fetch/src/data_fetch/cli.py`);嫌煩就用 framework 的
`workspace_app.tooling.dispatcher`(decorator 版,opt-in)。

> **絕對 import only。** tool package 的程式碼會在 prebuild 時被**複製／搬遷**,相對 import 一
> 搬就爆。ruff `TID252` + `ban-relative-imports = "all"` 會擋下來。

### 一個 package 有哪些 command？（沒有宣告檔）

**沒有任何外部設定檔宣告 command 清單**——它是 package **自我描述的程式碼**,由 launcher 的
**stage-1**(零參執行)吐出來:

- **多 command**——在 `commands/__init__.py` 的 **`COMMANDS` dict**。`cli.py` 的 dispatcher
  **iterate 這個 dict** 產生清單。新增一個 command = 這 dict 加一行 + 一個模組
  (`Args` + `DESCRIPTION` + `run`),不改別處。
  ```python
  # sample-tools/csv-column-summary/src/csv_column_summary/commands/__init__.py
  from csv_column_summary.commands import plot, summarise
  COMMANDS = {"summarise": summarise, "plot": plot}   # ← 這就是 command 的來源
  # cli.py: print(json.dumps([{"name": n, "description": m.DESCRIPTION} for n, m in COMMANDS.items()]))
  ```
- **單 command**——連 dict 都不必,直接在 `main()` 的 stage-1 寫死
  `[{"name": "data-fetch", ...}]`,也沒有 `commands/` 資料夾(範例:`data-fetch`)。

系統怎麼「知道」:**prebuild** 跑一次零參 `./launch`,把這份清單**凍結**成
`.workspace-tools/<name>/commands.json`,再對每個 command 跑 `./launch <cmd>` 把 schema 凍結成
`schemas/<cmd>.json`。host 端 `tooling/registry.discover_packages` 開機時**只讀這些凍結檔**,
從不 introspect package。所以 command 清單住在 package 自己的 `cli.py`/`COMMANDS` 裡,
`commands.json` 只是 prebuild 產出的**快照**——改了 command 就要重跑 prebuild 才會生效。

### 用 decorator 版 dispatcher（省掉手寫 `main()`）

上面的 `cli.py` 是**手寫** dispatcher(讓契約攤在眼前)。嫌煩就用 framework 的
`workspace_app.tooling.dispatcher.Dispatcher`——**opt-in**,用 `@d.command(name, description)`
註冊,再從 console_script entry point 呼叫 `d.main()`,三段 argv 路由它全包了:

```python
# sample-tools/<name>/src/<pkg>/cli.py — decorator 版（多 command，各自不同 Args）
import json
from typing import Literal
from pydantic import BaseModel, Field
from workspace_app.tooling.dispatcher import Dispatcher

d = Dispatcher()

class SummariseArgs(BaseModel):
    csv: str = Field(description="Path to the CSV file in the workspace.")

@d.command("summarise", "Summarise each column of a CSV ...")   # ← 註冊即等於「新增 command」
def summarise(args: SummariseArgs) -> None:
    ...
    print(json.dumps({...}))

class PlotArgs(BaseModel):                                       # 不同 command → 不同 Args
    csv: str = Field(description="Path to the CSV file in the workspace.")
    column: str = Field(description="Column to plot.")
    kind: Literal["hist", "box", "line"] = Field("hist", description="Chart type.")
    out: str = Field("plot.png", description="Output image path.")

@d.command("plot", "Plot one column of a CSV as an image.")     # ← 第二個 command
def plot(args: PlotArgs) -> None:
    ...
    print(json.dumps({"out": args.out}))

def main() -> None:        # pyproject.toml [project.scripts] 指向這裡
    d.main()               # stage-1 列出 summarise + plot / stage-2 各自 schema / stage-3 執行
```

每個 `@d.command` 各自綁一個 Args model,`d.main()` 就能對 `./launch summarise` 與
`./launch plot` 回不同的 schema——兩個 command 共用同一個 venv(依賴裝一次)。

要點:

- command 清單改由 **decorator 註冊**(取代手寫的 `COMMANDS` dict)——一樣是 package 自我描述的
  程式碼,stage-1 依名稱排序輸出,prebuild 凍結成 `commands.json` 的流程不變。
- handler **恰好一個參數**,annotation 必須是 **pydantic `BaseModel` 子類**;Args model 就從這個
  annotation 抽出來(單一真相來源:同時驅動 LLM 看的 JSON schema **與** 執行期驗證)。違反(參數
  數不對、annotation 不是 BaseModel)在**註冊時**就 `TypeError`——fail-loud,不會拖到執行期。
- Dispatcher 本身**零 domain 邏輯**、除 pydantic 外零依賴;不想用照樣手寫 `main()`,framework 不挑。

**exit code 是契約的一部分**(#674):`2` = 可以重試、`3` = 要有人先做一件事、`1` = 其他失敗;
`124` / `-9`(記憶體上限)/ `-11`(ABI)/ `126`·`127`(bundle 壞了)由平台產生並自動翻成人話給模型。
第一方工具走的是同一條路徑,細節見 [`tool-authoring.md`](tool-authoring.md)。

### 兩條路：vendor 進 repo，或外部作者自己發版

同一個 tool package 的**寫法完全一樣**（三段式契約、`uv.lock`、pydantic Args）。差別只在
**bytes 從哪來**：

| | 第一方（vendor 進 repo） | 第三方（#674） |
|---|---|---|
| 原始碼在哪 | 我們的 `sample-tools/` | 作者自己的 repo |
| 誰 build | 我們的 CI（烤進 sandbox-host image） | 作者的 CI（用我們的 builder image） |
| 怎麼宣告 | `PACKAGES` + `agent.tools` | `agent.external_tools`（名字 → artifact 網址）+ `agent.tools` |
| 換版本 | 改 repo → 重新發版 | **作者 push 就好**，我們什麼都不用做 |
| 新增／移除 | 改 repo → 重新發版 | 改 `app.json` → 重新發版 |
| 挑部分 command | `agent.tools` 的 colon 語法 | **同一套** colon 語法，一樣寫在 `agent.tools` |
| 執行時在哪 | `/opt/tools/builtin/<name>` | `/opt/tools/ext/<sha>`，掛成 `/.tools/<本地名>` |
| 誰核准 | 進 repo 這件事本身 | 一張平台簽的憑證，作者 build 前就要有 |

兩條路的 command 選取是同一段程式碼：一個 turn 把開機掃到的第一方套件、和這次 resolve 到的
第三方套件併成同一個 list 才做選取，所以 `"pkg"` 收整包、`"pkg:cmd"` 只收一個，兩邊行為一致。

第三方那條路的作者面文件是 [寫一支工具（外部作者）](tool-authoring.md)；
我們這邊要做的事在 [部署與客製化](deployment.md) 的 §15：一次性設定看 §15.1，
**把一支工具放進 `app.json`（含只給部分 command）看 §15.2**，
**一支工具什麼時候真的進到 sandbox 裡看 §15.3**，發憑證看 §15.7。

**兩條路都到不了「使用者自建」**——見下一節。

### 註冊 + prebuild + 授權（第一方）

1. **註冊來源**——在 `src/workspace_app/tooling/packages.py` 的 `PACKAGES` dict 加一行
   `"<name>": SOURCE_DIR / "<name>"`。
2. **Prebuild**——`uv run python scripts/prebuild_tools.py`。它為每個 package 建一個 relocatable
   venv + portable python + `launch`,並把 schema 固化成檔,產物落在
   `.workspace-tools/<name>/`(`commands.json` + `schemas/<cmd>.json` + `launch` + `python/` +
   `.venv/`)。以**內容 hash** 判斷是否 skip,改了原始碼要重跑。(`.workspace-tools-uvrun/`
   是給開發用的輕量 symlink 版。)
3. **授權**——在某個 app 的 `app.json` `agent.tools` 陣列列出它。用 colon 語法細選:
   `"csv-column-summary"` 收全部 command,`"csv-column-summary:plot"` 只收 `plot`。
4. **重啟** app——開機時 `tooling/registry.discover_packages` 掃 `.workspace-tools/` 建
   `PackageInfo`,`build_function_tools` 依 `allowed` 展開成 `FunctionTool`(扁平 command name
   撞名會在開機 raise)。tool 的 name/description/JSON schema 也會被
   `agent/tool_prompt.format_tools_for_prompt` render 進 system prompt 末段,免得小模型把
   function tool 當成 PATH 上的 shell binary。

`app.json` 是**上限**;profile 的 `_profile.json` 可再收窄成子集;per-item 的 `tool_prefs`
再做三態覆寫(#322 的三層 resolve,見 `apps/catalog.py`)。

### 讀使用者設的環境變數（#664）

使用者可以在 item 的聊天標頭按「環境變數」設定 `KEY=VALUE`,tool 用 `os.environ` 就讀得到——
這是**讓 dev 寫的 tool 拿到使用者自己的 credential / endpoint** 的機制,tool 端零額外 API:

```python
# sample-tools/<name>/src/<pkg>/commands/fetch.py
import os

def run(args: Args) -> str:
    token = os.environ.get("MY_API_TOKEN")
    if not token:
        # 指名變數,使用者才知道要去設哪一個。
        return "MY_API_TOKEN is not set — add it under the workspace's Env panel."
    ...
```

**套用範圍(很重要,誤解會變成「設了沒作用」)**

值是在**派送這個 tool 的那一次 `exec`** 上帶進去的,不是放在 sandbox 裡讓誰都能讀:

| 執行路徑 | 拿得到？ | 為什麼 |
|---|---|---|
| 被 agent 呼叫的 tool（`data-fetch` / `sci-plot` / …） | ✅ | 派送時把值放進那次 exec 的環境 |
| 同一個 tool 的圖表重繪（#285） | ✅ | 跟派送共用同一個 `_exec_tool` |
| `exec` 裡的 `python` / `python3*` | ❌ | 那是 agent 自己的指令,不是 tool 派送 |
| `exec` 裡的其他任何指令（`git` / `curl` / `bash -c 'echo $VAR'`） | ❌ | 同上 |

⚠️ **skill 的 `scripts/` 拿不到**——它們是走 `exec python` 跑的。使用者想在自己的 skill 裡用
自己的 key,這條路不通;需要 credential 的邏輯要升格成 tool-package。

其他邊界:

- **一個 item 一組。** 存在 item 記錄的 `env_vars` 欄位,不跨 item、不跨使用者,也**不會**進到
  app backend 自己的行程。
- **保留字放行。** 使用者設 `PYTHONPATH` / `HOME` / `PIP_USER` 會**蓋掉** carrier 的設定。
  這需要一點機制:值在 launcher 啟動**前**就在環境裡,而 launcher 自己的 `export` 在後面,
  所以派送時會多帶一個 `SANDBOX_USER_ENV_KEYS`,launcher 進場先把這些名字存起來、`exec` 前
  再放回去。否則就變成「存了、列得出來、卻沒作用」。tool 作者的意思是:
  **不要假設 carrier 的環境完好**,該用絕對路徑的地方用絕對路徑。
- **值原封不動。** 走的是行程環境,shell 沒有機會展開,含 `$`、反引號、`$(…)` 的值照字面送達。
- **AI 讀得到,擋不住。** tool 和 agent 跑在**同一個 uid**,所以 agent 可以在 tool 執行的當下
  讀 `/proc/<pid>/environ`。這裡**不是 secret store**。比起把值放在 sandbox 裡的檔案(agent
  隨時 `cat` 得到),窗口窄很多,但不是零——要真的隔開,得讓 tool 與 agent 用不同 uid。

### 說出你需要哪些變數(#750)

上一節的問題是:**使用者不知道要設哪些**。tool 一多,唯一的辦法是跑下去看它爆,再從錯誤訊息
反推名字。所以 tool 可以**手寫一份清單**放在 package 裡,prebuild 會跟 `commands.json` 一起
帶進 bundle,環境變數面板就會長出對應的欄位。

```json
// <package 根目錄>/env.json —— 和 pyproject.toml 同一層,整份都是選填的
// (vendor 進 repo 就是 sample-tools/<name>/env.json;外部作者放自己 repo 的根)
[
  { "name": "MY_API_TOKEN",
    "description": "在 https://internal/tokens 產生的個人 token",
    "required": true },
  { "name": "MY_API_BASE",
    "description": "自架站台才要改;預設打正式站" }
]
```

只有 `name` 是必要的。**寫得越多,使用者越好填;什麼都不寫,跟今天一模一樣**——這是刻意的,
沒有人會因為你沒寫而被擋住。

⚠️ **這是提示,不是閘門。** 沒有列進來的名字**不會**被擋掉:tool 照樣拿得到 item 的
**全部**變數(見上一節的「AI 讀得到,擋不住」——同一個原因,`_tool_env` 不分對象)。
所以漏寫一個名字的後果只是「面板少講一句話」,不是功能壞掉。

⚠️ **不寫 `required` 不等於「選填」。** 三種狀態是分開的:標 `true` 會被算進「還缺幾個」,
標 `false` 不會,**不標則兩者都不是**——面板會列出它但不催你。所以你只在真的想清楚時才標,
不用為了填欄位而亂猜。同樣地,**整個檔案缺席 ≠ 不需要變數**,面板會照實說「這個工具沒有列出
它需要什麼」,而不是說它不需要。

`env.json` 格式錯誤的話,**prebuild 會當場失敗並指名檔案**(你在自己的 build 上,改得掉);
但在別人的部署上 `discover_packages` 會**降級成「沒宣告」並記一條 warning**,不會讓對方的
服務開不起來——一份壞掉的提示不該變成一次停機。

### 用帳號密碼換出變數(#750,第二方)

有些值使用者**打不出來**:他知道自己的帳號密碼,而 tool 要的是拿它們去換來的 token。
這時面板可以出現一顆登入鈕,按下去跳出輸入窗,換到的變數**填進表單**(不會自動存,使用者
還是要按儲存)。**帳號密碼不會被儲存、不進 log、不回傳。**

實作這段邏輯的是**第二方**(照接縫插進來的自家人),不是 tool 作者:

```python
# yourdeploy/sap.py
from workspace_app.api.env_provider import IEnvProvider, InputField

class SapLogin(IEnvProvider):
    @property
    def id(self) -> str: return "sap-login"
    @property
    def label(self) -> str: return "SAP 正式站登入"
    @property
    def produces(self) -> frozenset[str]:
        return frozenset({"SAP_TOKEN", "SAP_HOST"})
    @property
    def inputs(self) -> tuple[InputField, ...]:
        return (InputField("user", "帳號"), InputField("password", "密碼", secret=True))

    async def resolve(self, values: dict[str, str]) -> dict[str, str]:
        token = await my_sap_client.login(values["user"], values["password"])  # 自己設逾時
        return {"SAP_TOKEN": token, "SAP_HOST": "sap.corp.example.com"}
```

```yaml
# config.yaml
server:
  env_providers:
    - yourdeploy.sap.SapLogin
```

⚠️ **tool 不會、也不能指名要用哪個方法。** 它只宣告變數**名字**,方法宣告它**產出**哪些名字,
平台用名字比對——`SAP_TOKEN` 對上了,鈕就出現。這個方向是刻意的:如果讓 tool 寫
`"filled_by": "sap-login"`,等於讓**第三方上架者決定我們的介面要向使用者索取哪一組憑證**,
而且兩個素未謀面的人共用一個識別字命名空間,撞名的落點是登入視窗(A 廠的密碼打進 B 廠的表單,
全程不報錯)。變數名字是 tool 的程式碼**本來就非寫不可**的東西,拿它當接點不新增任何命名空間。

其他要知道的:

- **回傳什麼就寫什麼**,包含沒有人宣告的名字——宣告本來就可能不完整,過濾掉反而會丟掉最需要留的。
- ⚠️ **回傳的值不能含換行。** 面板是以 `.env` 文字(一行一個變數)在編輯這些值,多行的值讀回來
  只會剩第一行。面板會**整包拒絕並指名是哪個變數**,而不是存下半截——被告知存不了還救得回來,
  拿到半張憑證救不回來。如果你要給的是 PEM,這條路送不了它,得用別的方式交給 tool。
- **自己設逾時**。平台不知道你的閘道多久算合理,在這裡定一個數字只會拒絕掉「只是有點慢」的請求。
- **丟例外 = 告訴使用者失敗**,面板保留他已經打好的其他內容。訊息裡**不要放 `values`**。
- **沒有設定任何方法 = 沒有鈕**,不是壞掉:每個變數都還是能手打,那條路永遠可用。
- 這顆鈕和手動存檔一樣要 `write_meta`。只能讀的參與者按不動——否則他能換出一個自己存不進去的
  token 並從回應裡讀走。

### 隨「按下送出的那個人」而變的變數(#714)

上面那組是**一個 item 一份、大家共用**的。有一種值它天生裝不下:**每個人不一樣的身分**——
使用者自己的 SSO session cookie、閘道加在請求上的 header。把它填進 `env_vars` 等於把小明的
登入憑證分享給這個 item 的每一個參與者(那個欄位 `read_meta` 就讀得到,而且不遮罩)。

所以有第二條來源:部署自己寫一個 `IRequestEnv`(`workspace_app/api/request_env.py`),用
`server.request_env` 指過去。平台**不認得任何 cookie 名字**——哪個 cookie、哪個 header、值是
什麼意思,全都是你們閘道的事實,所以整個判斷(包含白名單)都在你的 impl 裡:

```python
# mycorp/plugins.py  — 部署自己的套件,不在本 repo
from fastapi import Request
from workspace_app.api.request_env import IRequestEnv

class SsoCookieEnv(IRequestEnv):
    async def env_for(self, request: Request, *, user_id: str, item_id: str) -> dict[str, str]:
        session = request.cookies.get("SSO_SESSION")
        return {"MYCORP_SESSION": session} if session else {}
```

```yaml
server:
  request_env: "mycorp.plugins.SsoCookieEnv"   # 沒設 → 這個機制完全不存在
```

tool 端的讀法跟上面**一模一樣**(`os.environ`),它分不出值從哪來——這是刻意的。

規則:

- **不落地。** 值只活在觸發它的那一輪 turn,不寫進 item、不寫進任何儲存。
- **item 的設定蓋過它。** 同名時 `env_vars` 那格贏,而且沒有提示(要拿服務帳號的值壓過去做
  測試時就靠這個)。
- **只有聊天送出、以及 WUI 頁面的 `callTool` 有。** 兩者的共同點是:一次請求、一個人、
  結果只回給問的那個人,而且用完就沒了。
  - workflow 整條沒有:它會續跑、會被排程和上傳事件重跑,「第一步有、第二步沒有」是 UI 上
    看不出來的差別。goal driver(#615)自己續的那些回合同樣沒有——沒有請求就沒有身分,
    而且沒有任何存下來的東西可以繼承。
  - **WUI 的 rebuild 刻意沒有。** build 產出的 `dist/` 會落到永久儲存、並且被組進**每一個**
    看這個 item 的人拿到的文件裡;而 bundler 的工作就是把環境變數烤進產出物(Vite 的
    `loadEnv` 會把 `VITE_` 開頭的名字從 `process.env` 撈進 bundle)。per-request 的憑證
    進到那裡,就等於寫進別人下載得到的檔案——正好違反上面第一條「不落地」。build 需要的
    registry 憑證放 item 的 `env_vars`:那是所有能看這個頁面的人本來就有權拿到的東西。
  - **注意呼叫次數。** `callTool` 是頁面按一下就一次,不是一輪 turn 一次。impl 自己的
    rate limit 和延遲預算要照這個量抓。
- **`async def`,而且失敗就整輪不跑。** 需要拿 cookie 去外部換 token 是這個接縫存在的理由,
  那段延遲會直接坐在「按下送出」到「turn 開始」之間。impl 丟例外 → 這則訊息**送不出去**
  (使用者的訊息也不會被寫下來),因為這裡走的是身分:安靜地當作沒有,會讓 turn 以匿名身分
  跑完並交出一個看起來正確的答案。想降級的話,自己 `except` 回 `{}`——只有 impl 知道少了
  那個值還有沒有意義。
- ⚠️ **延遲要你自己設上限,平台不會幫你設。** 平台不知道你的閘道等多久算合理,設一個數字只會
  誤殺「只是慢」的請求。但這段等待很危險:此時使用者的訊息**還沒被寫下來**,一旦拖過 ingress
  的讀取逾時,閘道回 **504**,而前端把 504 當成「閒置代理切斷了 POST、turn 還在跑」→ 繼續等
  一個**從來沒開始**的 turn,輸入框就鎖死了。**在你自己那個呼叫上設 timeout**,逾時就丟例外
  (或回 `{}`)——一個看得見的拒絕,好過一個看不見的等待。
- **例外訊息不會回給前端,但會進伺服器 log。** 前端只拿得到一個固定的訊息——聊天送出是代碼
  `request_env_failed`(由聊天前端翻成人話),WUI 的 `callTool` 直接就是一句話(那個面板只
  顯示句子,代碼會變成畫面上的「(500)」)——因為只有你自己知道那串字是不是拿剛讀到的
  cookie 拼出來的。
  ⚠️ **反過來說,`raise RuntimeError(f"...{cookie}...")` 會把憑證寫進 log。**
  例外訊息裡只放「哪一步失敗」,不要放值。

### 為什麼沒有 user 自建 tool

新增 tool 要跑**任意 Python** 並可能持有 credential——把它開放給執行期使用者不安全,所以這是
**deploy-time 的動作**(plan §B.9 明列為非目標)。**「dev」不等於「我們」**:第三方作者走的也是
deploy-time 這條路——他們的 bytes 由一個人審過、登記進 `app.json`、跟著發版生效,而不是在
聊天室裡被生出來。使用者需要臨時計算時,走 **skill 的
`scripts/`**:它們跑在 workspace 內建的 python-stack(pandas / numpy / scipy / matplotlib);缺
的套件可以在 sandbox 裡 `pip install` 補上,但那是**這個 workspace 當下的狀態**——沒有鎖檔、
不可重現、workspace 一被回收就沒了。當一段 script 穩定、需要自訂依賴、或值得被驗證後重用,
那就是 **dev 把它升格成 tool-package** 的時機:tool-package 的依賴由 `uv.lock` 釘死並在
prebuild 時打包進 bundle,每個部署拿到的是同一組版本。

**細節**:[`subsystems/tooling-and-sandbox-host.md`](subsystems/tooling-and-sandbox-host.md)
(子系統參考)、[`plan-skills-and-tools.md`](plan-skills-and-tools.md) §B(設計與決策)。

---

## Skill（dev + user，格式相同）

一個 skill 是一份簡短、可重用的**方法論**指令檔——「某一類任務該怎麼做」——agent 會用
`read_skill(name)` 按需載入(progressive disclosure),不是一開始就塞進 system prompt。dev 端
與 user 端**用同一個 `SKILL.md` 格式**,只是註冊與生命週期不同。

### `SKILL.md` 格式與放置規則（兩端共用）

一個 workspace skill 就是 workspace 檔案樹裡的一個資料夾:

```
{workspace-root}/
  .skill/                    # ← 單數 .skill，不是 .skills
    triage-reflow/           # ← 資料夾名
      SKILL.md               # ← 檔名必須正好是 SKILL.md（大寫），且正好一層深
      references/            # 選用 — 內文指到時 agent 用 read_file 讀
      scripts/               # 選用 — agent 透過 exec 在 python-stack 上跑
```

```yaml
---
name: triage-reflow          # ← 必須等於資料夾名（triage-reflow），否則被跳過
description: 分流 reflow 缺陷。當用戶說「reflow」/「焊接不良」或開了空白工單時使用。
---

# 方法論本體（markdown）
```

body 硬上限 `SKILL_BODY_CAP = 50_000` 字元(兩端都套)。核心載入邏輯在
`src/workspace_app/apps/skills.py`。workspace skill 走 `WorkspaceFiles` 讀 workspace 檔案樹,
對應到共享 sandbox 的 `{sandbox.root}/{item_id}/root/.skill/…`,所以把檔案直接放進 sandbox root
也會被看到。它**每個 turn live 重讀**(不 cache),存進去下一輪就出現在 index。

!!! warning "手動放置 skill 的三個前提——任一違反就被**靜默跳過**（不報錯，只 log warning）"

    載入器（`workspace_skill_metas`）對壞掉的 skill 是**容忍**的:它只 skip 那一個、不弄壞整個
    index——代價是**不會有任何錯誤跳出來**告訴你放錯了。手寫時務必:

    1. **資料夾是 `.skill`（單數）。** 常數 `WORKSPACE_SKILL_DIR = ".skill"`;放進 `.skills/`
       永遠讀不到。而且 `SKILL.md` 必須正好在 `.skill/<name>/SKILL.md`(**一層深**,不能是
       `.skill/<name>/sub/SKILL.md`),檔名也必須正好是大寫 `SKILL.md`。
    2. **frontmatter 的 `name:` 必須等於資料夾名。** `name != dir_name` → log warning + skip
       (缺 `name:` 也 skip)。所以 `.skill/cowsay/SKILL.md` 裡就一定要寫 `name: cowsay`。
    3. **`description` 決定它會不會被觸發。** agent 在 index 只看得到 **name + description**,
       **看不到 body**。能不能命中使用者的話,完全取決於 description 寫得夠不夠貼近他們會怎麼問。
       「Render an ASCII-art cow speaking a message」這種含關鍵字、貼近提問的描述,「cow 怎麼 say」
       就會命中;描述模糊,agent 根本不會去 `read_skill`。

    **想繞開這三個坑**:別手擺檔——讓 agent 用 `save_skill(name, description, body)` 幫你寫。它會把
    name slug 化、資料夾名與 frontmatter `name:` 對齊、frontmatter 寫對,下一輪就自動進 index。

### dev 自建

1. 把 skill 放到 `sample-skills/<name>/`。
2. 在 `src/workspace_app/apps/shared_skills.py` 的 `SHARED_SKILLS` dict 註冊它。
3. 在某個 app 的 `app.json` `agent.skills` 列出這個名字(並在 `agent.tools` 授予
   `save_skill`,若要同時開放 user 共創)。

另有兩種內建位置:烤進 profile 的
`src/workspace_app/apps/<slug>/profiles/<profile>/.skill/<name>/`(隨每個新 workspace 內附,唯讀),
以及 repo 根的 `skills-lock.json`(把 skill 名對映到遠端 GitHub 來源)。`read_skill` 的解析順序是
**workspace(user) → shared(app.json) → profile**,前者 shadow 後者。package／profile skill 是在
system prompt build 時**靜態**列入 index(`apps/catalog.py`),workspace skill 則**每輪 live 注入**;
兩者同名時 workspace skill 蓋過 package skill。

### 調校 skill 的 guidance（第二方）

**好的 guidance 是跟模型綁定的**——對某個模型調好的 skill 內文,換一個模型就不是調好的。
所以部署方(第二方)必須有辦法用**自己的模型、自己的資料**改這份內文並看出差別。
`python -m workspace_app.skill_eval` 就是這條迴圈:

```
# 1. 把出貨的 guidance 倒成一個你可以改的檔
python -m workspace_app.skill_eval --dump-skill verify-number -o ./tune

# 2. 對情境評分,旁邊擺上「完全不給 skill」的對照組
python -m workspace_app.skill_eval --skill ./tune/SKILL.md \
    --scenarios sample-scenarios/verify-number --control -o ./tune/run-1

# 3. 改 ./tune/SKILL.md,重跑進 run-2,比對兩份報告
```

`--skill` 吃**註冊過的名字或一個路徑**,後者才讓第 3 步是一個迴圈而不是 fork 整個 repo。

**模型不用在命令列指定。** 那一輪是用 App 自己的解析路徑 `AppCatalog.resolve` 取得的——
跟真正的 turn 同一個呼叫——所以 model、endpoint、system prompt 全部來自
`config.yaml` + `app.json` + profile。要換模型就 `--preset <config 裡的 preset 名>`
(也就是 App model picker 上那些名字),不給就用該 App picker 的第一個。`--config` 指向
非預設位置的 config.yaml。

這一點不是潔癖:自己重組 prompt 那版漏掉了 `## Available skills` 索引,於是模型從頭到尾
沒被告知這個 skill 存在——「靠 `read_skill` 自動觸發」那條路因此**根本量不到**,而且不會
有任何東西報錯。

- **對照組是重點**:skill 過、而且不給 skill 也過的情境,什麼都沒證明。報告會直接點名,
  不會把它算成戰果。
- **評分是決定性的,不是 LLM judge**:「有沒有呼叫 `ask_user`」「答案有沒有指出 dtype」
  都有客觀答案;加一個 judge 只會多出一個需要先被校準的東西。情境用
  `must_call` / `must_not_call` / `must_mention` / `must_not_mention` 宣告,其中一個 phrase
  可以寫成候選清單,免得對用字過度敏感。
- 情境放 `sample-scenarios/<skill>/`,**不要**放進 skill 資料夾——`SKILL.md` 以外的任何檔案
  都會在第一次 `read_skill` 時複製進**每個**使用者的 workspace。
- system prompt 來自 app 自己的 `AppCatalog.resolve`(含 skills 索引),`exec` 輸出照
  `agent.tools._format_exec` 框、一輪只跑第一個 tool call,所以測的就是正式會送出的 guidance。
  它不模擬 specstar／sandbox jail／SSE／額度／工具授權——那些只會讓正式環境更寬鬆,
  所以這裡綠燈是「可以去做活體檢查」,不是「取代活體檢查」。

#### 寫你自己的情境

一個情境就是一個 `*.json`,放在你指給 `--scenarios` 的資料夾裡,它引用的資料檔放在同一層。

```json
{
  "name": "silent-dtype",
  "note": "給人看的:這個情境為什麼存在。只出現在報告裡,不影響判定",
  "data": ["silent_dtype.csv"],
  "prompt": "Compute mean - 3 sigma of thickness in silent_dtype.csv.",
  "expect": {
    "must_call": ["exec"],
    "must_mention": [
      "thickness",
      ["not a number", "not numeric", "object", "as text"]
    ]
  }
}
```

| 欄位 | 意義 |
|---|---|
| `name` | 報告與輸出資料夾用的識別字(必填) |
| `prompt` | 送給模型的那句話(必填) |
| `data` | 開跑前複製進 workspace 的檔案,相對於情境資料夾 |
| `note` | 給讀報告的人看的說明 |
| `expect.must_call` | 這些工具**每個都**要被呼叫過 |
| `expect.must_not_call` | 這些工具**一個都不准**被呼叫 |
| `expect.must_mention` | 最終答覆裡**每一項都**要出現 |
| `expect.must_not_mention` | 最終答覆裡**一項都不准**出現 |

`expect` 的四個欄位都可省略,省略就是「不在意」——一個情境只宣告它真的想主張的事。

`must_mention` / `must_not_mention` 的每一項可以是**一個字串**,或**一個候選清單**——清單
中任一個命中就算數。這是為了讓期望不要對用字過度敏感:你要主張的是「它有沒有指出這欄
不是數字」,不是「它有沒有剛好用 object 這個詞」。比對**不分大小寫**。

判定完全是決定性的,沒有 LLM judge:這些問題都有客觀答案,而加一個 judge 只會多出一個
需要先被校準的東西。想主張的事若無法寫成這四條,通常代表那個主張還沒被想清楚。

一個實務建議:**每個情境都放 `"must_call": ["exec"]`**。在模型腦中算出來的數字既不可重現
也無法查核,所以「絕不心算」是這類 skill 裡唯一有完全客觀測法的一條。

另外準備一個**乾淨的對照情境**——沒有任何缺陷、正確行為是「直接回答、什麼都別問」。
會叫狼來了的 guidance 實務上會被使用者關掉,而只有這種情境抓得到它。

### user 自建（#298）

在任何 workspace app 裡跟助理說「幫我做一個 skill」,agent 會載入內建的 `author-skill`
meta-skill,走**界定→抽取→草擬→審閱→儲存→收尾**六步,最後呼叫 `save_skill(name, description,
body)` 把檔寫進 workspace FileStore 的 `.skill/<name>/SKILL.md`(你永遠不必手動編輯)。它:

- **每個 turn live 重讀**(不 cache),存進去下一個 turn 就 `read_skill('<name>')` 可用;
- 只活在**這個 workspace**,靠 chat header 的 **Skills 面板**(`SkillsModal`)**下載**成資料夾 zip
  或**匯入**別的 workspace(端點 `GET /a/{slug}/items/{item_id}/skills`);
- 它的 `references/` / `scripts/` 能用,是因為它們就住在 sandbox 掛載的那個 workspace 裡。

**為什麼 user 自建 skill 安全又容易**:skill 是**被動 markdown**,本身不執行任何東西(頂多
agent 讀了照做),零執行風險,所以放手讓使用者跟 AI 隨意寫。這正是它與 workflow 的分水嶺。

**細節**:[`skills-authoring.md`](skills-authoring.md)(user 共創流程)、
[`plan-skills-and-tools.md`](plan-skills-and-tools.md) §A(dev 機制與決策)。

---

## Workflow（dev + user，形狀差異巨大）

一個 workflow 把多個 step(agent turn、sandbox 指令、human gate、有 journal 的副作用)串成一條
可重跑、可 resume、以 filesystem 為 journal 的自動化。**這是 dev 端與 user 端差最多的擴充面**——
因為 workflow 會**執行**而且握有特權。

### dev 自建：Python `run.py`（圖靈完備）

一個 dev workflow 是**一個 `async def run(wf, inputs)`** 加上 profile `_profile.json` 裡的一小段
manifest:

```
apps/<app>/profiles/<profile>/
  _profile.json                  # 宣告 workflow（id、title、phases…）
  workflows/<id>/run.py          # async def run(wf, inputs) — orchestration
```

控制流就是普通 Python(`for` / `if` / `await`),跑在一套 step 函式庫之上:`agent_step` /
`agent_write_step`(有 gate 的 LLM turn)、`sandbox_node`(無 LLM 的指令)、`human_gate`
(produce→review→commit 接縫)、`wf.map`(平行 for-each)、`wf.ingest_to_collection` /
`wf.upsert_context_card`(有 journal、idempotent 的副作用)、以及自訂 gate。它是 **trusted
Python**——持有 turn engine、sandbox 生命週期、capability credential。用
`python -m workspace_app.workflow new/check` scaffold 與靜態檢查。

**細節**:[`workflows-authoring.md`](workflows-authoring.md)(block catalog + how-to)、
[`workflows.md`](workflows.md)(完整規格)。

### user 自建：`workflow.json` 降階 DSL（#323，最難的一塊）

**為什麼不能像 skill 一樣放手讓使用者寫?** 因為 skill 被動、workflow 會**執行**,而且
orchestration 握有特權 capability。把**使用者寫的 Python 跑進 trusted API 不安全**。所以使用者
**不能寫 code**——他們得到的是一個**降階、非圖靈完備的 JSON DSL**,由一個 trusted 的**通用
interpreter** `run()` 讀它、把每個 step 的欄位當參數 dispatch 到上面那批**既有的** primitive。
沒有任何使用者 *code* 跑進 API。

一份完整的 `workflow.json` 長這樣(把上傳檔分流進 collection):

```jsonc
{
  "schema": 1,
  "id": "ingest-logs",
  "title": "File uploads into collections",
  "phases": [
    { "id": "classify", "title": "Classify" },
    { "id": "review",   "title": "Review" },
    { "id": "commit",   "title": "Commit" }
  ],
  "config": { "collections": ["logs", "specs"] },
  "steps": [
    { "type": "map", "over": "uploads/*", "as": "file", "phase": "classify", "do": [
      { "type": "agent",
        "prompt": "Read {file}. Pick a collection from {config.collections}; write a digest. Output JSON {collection, digest, source}.",
        "out": "plan/{file}.json",
        "tools": ["read_file", "ask_knowledge_base"],
        "check": { "choice_in": { "path": "plan/{file}.json", "key": "collection", "allowed": "{config.collections}" } },
        "retries": 2 } ] },
    { "type": "gate", "phase": "review", "title": "Approve filing these?", "summary_from": "plan/*.json", "allow": ["approve", "reject"] },
    { "type": "map", "over": "plan/*.json", "as": "p", "phase": "commit", "do": [
      { "type": "capability", "call": "ingest_to_collection", "collection": "{p.collection}", "path": "{p.source}" } ] }
  ]
}
```

DSL 的天花板(刻意收窄):

- `steps` 是有序清單;step `type` ∈ `agent` / `sandbox` / `gate` / `capability` / `map`
  (`map` 是**唯一**的迴圈,**one-level、不可巢狀**)。
- `{x}` / `{x.field}` 是**唯讀字串代入**(非任意運算式、**no eval**);當 `{x.field}` 的 `x`
  指向一個 `.json` 檔時,它會讀檔取欄位——這正是「agent 記下決定 → 資料 → 派給 capability」的
  decision/action routing。
- `check` 是宣告式的 gate builder(`file_nonempty` / `choice_in` / `collection_has`);branch 用
  資料 routing。**沒有** revise-loop / branch 基本元素 / 巢狀 map——那些留給 dev 的 `run.py`。
- **安全不變式**:「使用者 workflow 能做的,**恰好等於它的作者親手能做的**」。capability 在
  **captured user 的 authz scope** 下跑;使用者的 `sandbox` step 是 **compute-only**(不給
  run-scoped credential),所以副作用永遠只走受控的 capability primitive。authoring 不產生任何新
  權限。

**共創與生命週期**(照搬 skill 模型):一個 `author-workflow` meta-skill 引導 AI 起草 DSL;一個
`save_workflow` tool 在寫入前**驗證**(schema、phase 一致、`tools` ⊆ profile 上限、capability 在
允許清單、`check` 格式)並把無效的 DSL **退回原因讓 AI 修**。它存進
`<workspace>/.workflows/<id>.json`(FileStore、item-local、live 讀,同名 **shadow** 掉 package
workflow,**不是** specstar resource)。FE 的 **Workflows 面板**(`WorkflowsModal`,掛在
AgentPanel)每列一個 **Run** + 下載／匯入;使用者按 Run,既有的 Run 端點／orchestrator／journal／
gate 機制原樣把它跑起來。

當一個 user workflow 穩定了,dev 可以把它**升格**成 profile 內建(免 transpile,因為同一個
interpreter 也跑 package 端的 `workflow.json`)——步驟見下面
[〈把 user 的 `workflow.json` 升格為 profile 內建〉](#promote-workflow-json)。

**細節**:[`workflows.md`](workflows.md) §22、[`plan-issue-323.md`](plan-issue-323.md)。

### dev `run.py` ↔ user `workflow.json` 對照

| 面向 | dev `run.py` | user `workflow.json` |
|---|---|---|
| 形式 | trusted Python | 宣告式 JSON DSL（資料） |
| 圖靈完備 | 是 | **否**（刻意） |
| 控制流 | 任意 `for` / `if` / `await`、巢狀、revise-loop、branch | 只有 `map`（one-level）+ 資料 routing |
| 副作用 | 任意 capability + 自訂 `sandbox_node` | 受控 capability allowlist；`sandbox` 為 compute-only |
| 存放 | `apps/…/profiles/<p>/workflows/<id>/run.py`（repo） | `<workspace>/.workflows/<id>.json`（FileStore） |
| 建立方式 | 手寫 + `workflow new/check` scaffold | 跟 AI 共創 → `save_workflow` |
| 驗證 | `check` CLI + 開機 `exec` | `save_workflow` 存檔時 validator |
| 誰能建 | dev（commit 進 repo） | 能存取該 item 的任何 user |
| 執行者 | 它自己的 `run()` | trusted 通用 interpreter dispatch 到同一批 primitive |

底層是**同一批 step primitive、同一套 filesystem-journal + input-hash 執行模型**;只是 authoring
的**表面**天差地遠——一邊是任意 Python,一邊是受限資料。

---

## View Kind（#698）——維運方自建的畫面

一個 view kind 是一個名字,workspace 裡的 `*.ai.yaml` 用 `view:` 指名它:

```yaml
view: csv-table
title: Wafer yield
source: /data/wafer.csv     # 這個 key 是那個 kind 自己的
```

平台內建 `table` / `board` / `gantt` / `health`;維運方自建的放 `web/src/ext/`,寫一個吃
`EntityViewProps` 的 React 元件 + 一行 `registerViewKind({ kind, Component })`。
`web/src/main.tsx` 有一行 `import "./ext";` 把它們掛上去,**不需要動 `ext/` 以外的檔案**。

三件跟其他擴充面不同、值得記住的事:

- **不綁 entity。** 一個 kind 可以完全不碰 entity,只讀 workspace 檔案（`useFileBuffer` /
  `useFileService`）——這是主要用法。要畫 entity 紀錄才宣告 `needsEntity: true`,那時 view 檔
  就必須寫 `entity:`。所以一個**完全沒有 `.entity/` 的 app**（rca）照樣用得上。
- **`ext/` 只能從 `renderers/entity/public` import**,由 `web/src/ext/imports.test.ts` 守著。
  這不是潔癖:它讓「動了這個介面會影響誰」在改的當下就看得見。
- **沒有版號、不承諾介面不變。** 因為程式碼在同一個 repo、同一次 CI 編譯,改壞了會在編譯期
  就紅,而不是等使用者打開畫面。這是同 repo 換來的保護。

註冊撞名會直接丟例外(開機就爆),不會靜默覆蓋——兩個元件搶同一個 `view:` 沒有正確答案,
而靜默的勝負取決於 import 順序。

作法見 [寫一個 View Kind（維運方）](view-kind-authoring.md)。範例 kind 與其測試在
`web/src/ext/CsvTableView.tsx`。

**尚未開放**:檔案預覽層（`web/src/renderers/registry.ts`,決定 `.csv` / `.md` 這類副檔名用哪個
renderer）還不能第二方註冊,#698 刻意只開 view kind 這一層。

---

## 升格：把 user 自建的東西變成內建

三條 user 路徑都能被 dev **promote** 成 profile 內建,出貨給該 app 的所有人:

- **skill** → dev 把資料夾 commit 進 `apps/<slug>/profiles/<profile>/.skill/<name>/`。v1 只帶
  `SKILL.md` 本體,不帶掛載的 `references/` / `scripts/`。
- **skill 的 script** → 當它需要自訂依賴或值得驗證後重用,dev 把它**升格成 tool-package**
  (見上面 Tool 段)。

### 把 user 的 `workflow.json` 升格為 profile 內建 {#promote-workflow-json}

**同一個 trusted interpreter 服務兩層**:一個 *package* workflow 可以是 `run.py`(trusted
Python)**或** `workflow.json`(被 interpret)。所以升格**免 transpile**——就是把資料檔搬進 repo:

1. **拿到 json** — 從 FE 的 Workflows 面板 **Download**,或直接取
   `<workspace>/.workflows/<id>.json`。
2. **放進 profile** — 落在 `apps/<app>/profiles/<profile>/workflows/<id>/workflow.json`,也就是
   同名 `run.py` 會住的**同一個資料夾**(資料夾名 = workflow id)。內容原封不動,**不必改寫成
   Python**。
3. **在 `_profile.json` 宣告它** — 加一條 `workflows: [...]` 條目(id、title、phases…),跟任何
   package workflow 一樣。v1 的 discovery 是**宣告驅動**的,Run picker 讀 `_profile.json`;所以
   「升格 = 丟 json **加** 補一行條目」。(不必改 `_profile.json` 的自描述 drop-in 掃描是 v2
   follow-up。)
4. **重啟** — 開機時 `discovery.load_run_callable(app, profile, id)` 解析這個 id:**先找
   `workflows/<id>/workflow.json`,有就交給 interpreter**,沒有才 fall 回 `run.py`——**兩者同時
   存在時 JSON 勝**(#323 Q6)。開機與 CI 的 bundled-clean gate 會用 `validate_workflow_profiles`
   驗證這份 DSL(schema／phase 一致),壞掉就 fail-loud。

現成的活範例:`apps/playground/profiles/dsl/`(`_profile.json` + `workflows/file-uploads/workflow.json`)。

---

## 原始碼與細節文件（快速索引）

| 面向 | dev 入口 | user 入口 | 細節文件 |
|---|---|---|---|
| Tool | `tooling/packages.py::PACKAGES`、`scripts/prebuild_tools.py`、`tooling/{prebuild,registry}.py`、`sample-tools/` | —（無） | `subsystems/tooling-and-sandbox-host.md`、`plan-skills-and-tools.md` §B |
| Skill | `sample-skills/`、`apps/shared_skills.py::SHARED_SKILLS`、`apps/skills.py` | `sample-skills/author-skill/`、`agent/tools.py::save_skill_impl`、`SkillsModal.tsx` | `skills-authoring.md`、`plan-skills-and-tools.md` §A |
| Workflow | `apps/…/workflows/<id>/run.py`、`workspace_app.workflow`、`workflow new/check` | `sample-skills/author-workflow/`、`save_workflow`、`workflow/dsl.py`、`WorkflowsModal.tsx` | `workflows-authoring.md`、`workflows.md`（§22 = user DSL）、`plan-issue-323.md` |
