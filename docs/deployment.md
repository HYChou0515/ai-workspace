# 部署與客製化指南（Deployment Guide）

本文說明如何部署這個 RCA 應用，以及如何把每一個可抽換的元件（sandbox、agent、
agent config、workspace 範本…）換成你自己的實作。

> 架構原則：所有層都透過 **Protocol** 連接，靠 `create_app(...)` 注入。要換掉
> 任何一塊，就寫一個新的實作、在你自己的進入點把它注入進去即可——核心程式碼不用動。

---

## 1. 總覽：可抽換的接點

```
React SPA (web/dist) ─► FastAPI app (create_app) ─┬─► AgentRunner Protocol   ← 換 LLM / agent 行為
                                                  ├─► Sandbox Protocol       ← 換執行環境
                                                  ├─► FileStore Protocol     ← 換檔案儲存
                                                  ├─► AgentConfig (resource) ← 換模型 / prompt / 工具
                                                  ├─► template profiles      ← 換新調查的起始檔案
                                                  └─► KB embedder/chunker/檢索 LLM ← 換知識庫嵌入與檢索（§8）
```

`create_app` 的簽章（`src/workspace_app/api/app.py`）：

```python
def create_app(
    *,
    spec: SpecStar | None = None,   # 資料層；不給就自動建一個
    sandbox: Sandbox,               # 必填：執行環境
    filestore: FileStore,           # 必填：檔案儲存
    runner: AgentRunner,            # 必填：agent 執行器（RCA 與 KB 共用）
    kb_embedder: Embedder | None = None,  # KB 嵌入；不給用離線 HashEmbedder（非語意）
    kb_chunker: Chunker | None = None,    # KB 切塊；不給用 FixedTokenChunker
    kb_llm: Llm | None = None,            # 給了才啟用 multi-query / HyDE / rerank
    spa_dist: Path | None = None,   # 前端靜態檔；預設找 <repo>/web/dist
    idle_timeout: timedelta = timedelta(hours=8),       # 閒置多久回收 sandbox
    idle_check_interval: timedelta = timedelta(seconds=60),
) -> FastAPI: ...
```

預設的 wiring 範例就是進入點 `src/workspace_app/__main__.py`——要客製化，**複製它改一份**
就好。

### 可抽換的 Protocol 一覽

每個接點都是一個 **Protocol**（結構型別、duck typing，**不需要繼承任何基底類別**）。要換實作，
就實作下表的 method 然後注入。**每個 method 的契約寫在原始碼的 docstring**（參數、回傳、要丟的
例外、不變式），那是權威來源——下面只列要實作哪些：

| Protocol | 檔案 | 要實作的 method | 注入 |
|---|---|---|---|
| `Sandbox` | `sandbox/protocol.py` | `create` / `kill` / `exec` / `upload` / `download` / `walk` / `expose_port` | `create_app(sandbox=…)`（§4） |
| `FileStore` | `filestore/protocol.py` | `write`/`read`/`ls`/`exists`/`delete`、`mkdir`/`rmdir`/`is_dir`/`listdir`、`dirty_paths`/`clear_dirty` | `create_app(filestore=…)`（§5） |
| `AgentRunner` | `api/runner.py` | `run`（async generator，yield `AgentEvent`） | `create_app(runner=…)`（§6） |
| `Embedder` | `kb/embedder.py` | `dim` / `embed_documents` / `embed_query` | `create_app(kb_embedder=…)`（§8） |
| `Chunker` | `kb/chunker.py` | `chunk` | `create_app(kb_chunker=…)`（§8） |
| `Llm`（KB 檢索增強） | `kb/llm.py` | `complete` | `create_app(kb_llm=…)`（§8） |

> 慣例：先讀該 Protocol 的 docstring 了解每個 method 要保證什麼，再實作。`Settings` + `get_*`
> factory（§3）只負責「用環境變數選內建實作」；你的全新實作直接傳進 `create_app` 即可，不必
> 動 factory。

---

## 2. 快速啟動（預設組合）

```bash
# 後端依賴
uv sync

# 前端打包（產生 web/dist，後端會自動掛載到 /）
cd web && pnpm install && pnpm run build && cd ..

# 啟動（API + SPA 一起跑在 127.0.0.1:8000）
uv run python -m workspace_app
```

預設組合是：`LocalProcessSandbox` + `MemoryFileStore` + 載入 RCA system prompt 的
`LitellmAgentRunner`，模型走本機 Ollama 的 Qwen3。

> 沒有 `web/dist` 也能跑，只是 `/` 不會有前端；API 仍可用。

> **要調 `config.yaml` 的旋鈕**（換模型、sandbox `kind`、多 pod、環境變數…）看
> **[設定指南 configuration.md](configuration.md)**——本頁專講「用程式/factory 換整塊實作」，
> 設定指南專講「用 YAML 調哪顆旋鈕」。逐行參照在 [`configs/config.example.yaml`](https://github.com/HYChou0515/ai-workspace/blob/master/configs/config.example.yaml)。

---

## 3. 自訂進入點

「**選哪個實作**」集中在 **組裝根**：`src/workspace_app/factories.py` 的 `Settings`
（一律從環境變數讀）+ 一組 `get_*(settings) -> Protocol` factory。預設進入點
`__main__.py` 只是薄薄一層：`Settings.from_env()` → 呼叫 factory → 餵進 `create_app`。
`create_app` 與 app 內部**只依賴 Protocol**，不認得任何實作，也不認得 `Settings`。

最常見的客製化「不用寫程式」——設環境變數即可（完整清單見 `factories.Settings`）：

```bash
SANDBOX_KIND=docker FILESTORE_KIND=specstar \
KB_EMBED_MODEL=ollama/bge-m3 KB_LLM_MODEL= \
APP_HOST=0.0.0.0 APP_PORT=8000 \
uv run python -m workspace_app
```

要在程式裡完全掌控（換成 factory 不認得的實作、或自組 `Settings`），**自己寫一支進入點**：

```python
# my_deploy.py
import uvicorn
from workspace_app.api import create_app
from workspace_app.factories import (
    Settings, get_spec, get_sandbox, get_filestore, get_runner,
    get_embedder, get_chunker, get_kb_llm,
)

def main() -> None:
    s = Settings.from_env()              # 或直接 Settings(sandbox_kind="docker", ...)
    spec = get_spec(s)
    app = create_app(
        spec=spec,
        sandbox=get_sandbox(s),          # ← 換實作就改 SANDBOX_KIND，或這裡塞你自己的
        filestore=get_filestore(s, spec),
        runner=get_runner(s),
        kb_embedder=get_embedder(s),
        kb_chunker=get_chunker(s),
        kb_llm=get_kb_llm(s),            # None → 停用 multi-query/HyDE/rerank
    )
    uvicorn.run(app, host=s.host, port=s.port)

if __name__ == "__main__":
    main()
```

```bash
uv run python my_deploy.py
```

> 寫了一個全新的實作（例如自家的 `Sandbox`）但不想擴充 factory？直接把它傳進
> `create_app(sandbox=MyRemoteSandbox(...), ...)` 即可——`create_app` 收的就是 Protocol。
> factory 只是「正式環境用環境變數選內建實作」的便利層；**測試一律直接注入 Mock/Scripted，
> 不走 factory**。

---

## 4. 換 Sandbox（執行環境）

Sandbox 是 agent `exec` 工具實際跑指令的地方。Protocol 在
`src/workspace_app/sandbox/protocol.py`：

```python
class Sandbox(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...
    async def kill(self, handle: SandboxHandle) -> None: ...
    async def exec(self, handle, cmd: list[str],
                   on_output: OutputSink | None = None) -> ExecResult: ...
    async def upload(self, handle, data: bytes, remote_path: str) -> None: ...
    async def download(self, handle, remote_path: str) -> bytes: ...
    async def walk(self, handle, root: str) -> WalkResult: ...  # .files + .dirs
    async def expose_port(self, handle, container_port: int) -> tuple[str, int]: ...
```

> `walk` 一次遍歷回傳兩半:`files`(`FileEntry`)與 `dirs`(純路徑)。目錄不用
> `FileEntry` 表示——目錄沒有內容,`size`/`version` 對它沒有意義,而且把目錄當成
> 檔案項交給呼叫端,mirror 會去下載它、配額會去計費它。`dirs` **包含沒有任何檔案的
> 目錄**:空目錄不出現在任何檔案路徑裡,所以下游無法從 `files` 反推。

> `on_output` 是**即時輸出**的 sink：長時間執行的指令會邊跑邊把 stdout 丟給它，
> run history 才能即時顯示。自己實作時，沒有串流需求可以在指令結束時一次性呼叫
> `on_output(stdout)`（`DockerSandbox` 就是這樣）。

內建三種：

| 實作 | 用途 | 隔離 |
|---|---|---|
| `MockSandbox` | 測試用、純記憶體 | 無（不真的執行） |
| `LocalProcessSandbox` | VM/devcontainer 單機部署（**預設**） | 有 user namespace 時自動 chroot 隔離 |
| `DockerSandbox` | 每個 sandbox 一個容器 | 容器級 |

### LocalProcessSandbox 的隔離

```python
LocalProcessSandbox(
    root_dir=None,        # 工作目錄根；預設 /tmp/workspace-app-sandbox
    exec_timeout=60.0,    # 單一指令逾時秒數（逾時會 kill，但保留已輸出的部分）
    isolate=None,         # None=自動偵測；True=強制隔離；False=直接在 host 跑
)
```

- `isolate=None`（預設）：偵測到 **unprivileged user namespace** 可用時，每個指令
  會在 user+mount namespace 內 chroot 到 sandbox 目錄執行——此時 `/` 就是 workspace，
  agent 用 `/script.py` 這種絕對路徑能正確解析，`/usr`、`/etc` 以唯讀掛入保護 host，
  host 檔案系統不可見。偵測不到（如某些受限環境）時自動退回直接在 host 跑（無隔離，
  絕對路徑會打到真正的 root）。
- 需求：`unshare` 指令、且 `kernel.unprivileged_userns_clone=1`（多數現代 Linux 預設開）。
- 強制關閉隔離：`LocalProcessSandbox(isolate=False)`。

### 寫你自己的 Sandbox

實作上面的 Protocol（例如接 Firecracker、gVisor、遠端 runner、K8s Job…），然後注入：

```python
app = create_app(sandbox=MyRemoteSandbox(...), filestore=..., runner=...)
```

只要符合 Protocol 的 method 簽章即可，不需要繼承任何基底類別（duck typing）。

---

## 5. 換 FileStore（檔案儲存）

FileStore 是 workspace 檔案的永久儲存（與 sandbox 解耦：純檔案操作不會開 sandbox）。
Protocol 在 `src/workspace_app/filestore/protocol.py`，重點 method：
`write / read / ls / exists / delete`、目錄類 `mkdir / rmdir / is_dir / listdir`、
以及給 sandbox 同步用的 `dirty_paths / clear_dirty`。

內建：

| 實作 | 特性 |
|---|---|
| `MemoryFileStore` | 純記憶體，**重啟即清空**（預設、最簡單） |
| `SpecstarFileStore` | 存進 specstar，重啟後仍在（代價：`/openapi.json` 會多出約 19 條內部檔案 CRUD 路由） |

要永久保存就換成：

```python
from workspace_app.filestore.specstar_impl import SpecstarFileStore
app = create_app(spec=spec, filestore=SpecstarFileStore(spec), sandbox=..., runner=...)
```

自己接外部儲存（S3、DB…）就照 Protocol 實作一個新類別。

---

## 6. 換 AgentRunner / Agent 行為

AgentRunner 是「scripted 測試」與「真 LLM」之間的抽換點。Protocol 在
`src/workspace_app/api/runner.py`：

```python
class AgentRunner(Protocol):
    def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]: ...
```

`run` 是個 async generator，逐一 yield `AgentEvent`（見 `src/workspace_app/api/events.py`：
`MessageDelta`、`ToolStart`、`ToolEnd`、`ToolLog`、`AgentMetrics`、`RunDone`…）。

內建：

- `LitellmAgentRunner`（production）：包 OpenAI Agents SDK + LiteLLM，支援 Ollama 與各家
  hosted 模型。建構參數：

  ```python
  LitellmAgentRunner(
      config=default_rca_agent_config(),  # 預設 AgentConfig（模型 + system prompt + 工具）
      max_retries=2,                      # 工具/格式錯誤時自動帶提示重試的次數
      max_turns=10,                       # 單一回合最多幾個 agent turn（超過視為未收斂）
  )
  ```

- `ScriptedAgentRunner(events=[...])`（測試/開發）：吐固定事件序列，不需要真 LLM。

要完全自訂 agent 行為（換框架、加 RAG、改事件流），就實作 `AgentRunner` Protocol 並
注入。只要 yield 的是前端認得的 `AgentEvent`，前端不用改。

---

## 7. AgentConfig（模型 / prompt / 工具 / 建議詞）

`AgentConfig`（`src/workspace_app/resources/agent_config.py`）描述一個「agent 人格」：

```python
class AgentConfig(Struct):
    name: str
    model: str = "ollama_chat/qwen3:14b"   # LiteLLM 模型字串（見下）
    system_prompt: str = ""
    suggestions: list[str] = []            # agent 面板上的快捷提問 chips
    allowed_tools: list[str] = []          # 空 = 全部工具；給清單則限制
    env: dict[str, str] = {}
    sandbox_image: str = "workspace-app/sandbox:py312-ds"  # DockerSandbox 用
    idle_timeout_seconds: int = 28800       # 8 小時
```

可用的工具名稱（`allowed_tools`）：`exec`、`read_file`、`write_file`、`ls`、
`exists`、`delete_file`、`ask_knowledge_base`（RCA 查 KB，預設工具集已含）；
`kb_search` 是 KB agent 專用、需 retriever，不在 RCA 預設集（見 §8）。

### 模型字串（LiteLLM）

`model` 直接交給 LiteLLM 依前綴分派：

| 目標 | `model` 範例 | 需要的環境變數 |
|---|---|---|
| 本機 Ollama | `ollama_chat/qwen3:14b` | `OLLAMA_API_BASE`（預設 `http://localhost:11434`） |
| Anthropic | `claude-opus-4-7` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` |
| 其他 | 見 LiteLLM 文件 | 各家對應 key |

> 預設方向（見專案慣例）：AI/agent 應用優先用 **LiteLLM + 本機小型 Qwen（Ollama）**，
> 而非 hosted。要 hosted 只要改 `model` 字串並設好對應的 API key 環境變數。

### 預設 AgentConfig 從哪來、怎麼換

啟動時 `_seed_agent_configs`（`api/app.py`）會在「一個都沒有」時種兩個預設：
`RCA · Qwen3 (local)` 與 `RCA · Claude Opus`，前端 agent 面板的 picker 就是讀這些。

要換成你自己的清單，最乾淨的做法是**在你的進入點，建好 app 後自己塞**：

```python
from workspace_app.resources import AgentConfig

app = create_app(spec=spec, sandbox=..., filestore=..., runner=...)

rm = spec.get_resource_manager(AgentConfig)
rm.create(AgentConfig(
    name="我的 Agent · Llama3",
    model="ollama_chat/llama3:8b",
    system_prompt=open("my_prompt.md", encoding="utf-8").read(),
    suggestions=["分析這份 log", "畫魚骨圖", "起草 RCA 報告"],
    allowed_tools=["read_file", "ls", "exec"],   # 例：唯讀＋執行，不給寫/刪
))
```

> 注意：預設 seeding 只在「目前沒有任何 AgentConfig」時才跑，所以你自己塞的不會被覆蓋；
> 若用 `SpecstarFileStore`/持久化 spec，重啟後你塞的也還在。若用記憶體 spec，請每次啟動都塞。

`LitellmAgentRunner(config=...)` 的 config 是「沒指定時的後備人格」；前端為某個調查
**指定** agent 時，會以該調查綁定的 `AgentConfig` 覆蓋（見 `_resolve_agent_config`）。

---

## 8. 知識庫（KB）：embedder / chunker / 檢索 LLM / 環境變數

KB 的「智慧」分三塊，都可由 `create_app` 注入（不給就用安全的離線預設）：

- **`kb_embedder`（`Embedder` Protocol，`kb/embedder.py`）**——把文字轉成向量。預設
  `HashEmbedder`：決定性但**非語意**（只夠跑離線/測試）。正式請用 `LitellmEmbedder`。
- **`kb_chunker`（`Chunker` Protocol，`kb/chunker.py`）**——切塊。預設 `FixedTokenChunker`。
- **`kb_llm`（`Llm` Protocol，`kb/llm.py`）**——**給了才會**在檢索時啟用 multi-query 擴展、
  HyDE、LLM rerank；不給就只做 dense+BM25 混合檢索。

預設進入點 `__main__.py` 已用環境變數接好 `LitellmEmbedder` + `LitellmLlm`：

| 環境變數 | 預設 | 說明 |
|---|---|---|
| `KB_EMBED_MODEL` | `ollama/qwen3-embedding` | 嵌入模型（LiteLLM 字串）。用 `bge-m3` 就設 `ollama/bge-m3` |
| `KB_EMBED_DIM` | `1024` | 儲存向量寬度，**必須等於模型輸出維度**；改了要重新索引 |
| `KB_LLM_MODEL` | `ollama_chat/qwen3:14b` | KB agent ＋ 檢索增強用的聊天模型 |
| `KB_QUERY_PREFIX` / `KB_DOC_PREFIX` | `""` | 非對稱指令前綴（部分嵌入模型需要） |

```bash
# 例：用 bge-m3（1024 維，與預設 KB_EMBED_DIM 相符）
docker compose exec ollama ollama pull bge-m3
KB_EMBED_MODEL=ollama/bge-m3 uv run python -m workspace_app
```

要在自己的進入點完全掌控，直接注入實作：

```python
from workspace_app.kb.embedder import LitellmEmbedder
from workspace_app.kb.llm import LitellmLlm
from workspace_app.resources.kb import EMBED_DIM

app = create_app(
    sandbox=..., filestore=..., runner=...,
    kb_embedder=LitellmEmbedder("ollama/bge-m3", dim=EMBED_DIM),
    kb_llm=LitellmLlm("ollama_chat/qwen3:14b"),   # 省略則停用 multi-query/HyDE/rerank
)
```

要寫自己的 embedder/chunker，實作對應 Protocol 即可（`LitellmEmbedder` 繼承
`_PrefixedEmbedder`，只需提供 `_embed` 與 `dim`）。

> **維度一致性**：`KB_EMBED_DIM` 決定 `DocChunk.embedding` 的 `Vector` 寬度，在 import 時就定
> 下。換成不同維度的模型，必須同步改 `KB_EMBED_DIM` **並重新上傳/索引**所有文件——舊向量是
> 用舊寬度存的。沒有真 embedder 時退回 `HashEmbedder`（非語意，只能驗證接線、不能驗品質）。

---

## 9. Workspace 範本（新調查的起始檔案）

開新調查時，會把某個**範本 profile**的檔案 seed 進該調查。Profile 就是
`src/workspace_app/rca/templates/` 底下的一個子資料夾，picker 會自動列出所有子資料夾。

現有 profiles：

- `default/`：使用者自有內容（目前是單一 `SOP.md`）。
- `methodology/`：空白骨架（`brief` / `5-why` / `fishbone` / `report.v1`）。
- `smt-reflow-example/`：完整範例。

### 加一個你自己的 profile

```bash
mkdir -p src/workspace_app/rca/templates/my-profile
# 放進任意檔案；重新部署後 picker 會自動出現 "my-profile"
```

命名規則（`src/workspace_app/rca/templates/__init__.py`）：

- `*.tpl`：會用該調查的欄位做 `string.Template` 變數替換，再把 `.tpl` 去掉落地
  （例如 `brief.md.tpl` → `/brief.md`）。可用變數：`title`、`owner`、`severity`、
  `status`、`product`、`description`、`members`、`topics`。佔位符用 `$name` / `${name}`，
  **打錯字會直接報錯**（不會默默輸出 `$foo`）。
- `_prompt.md`（**強烈建議放一份**）：這個 profile 的 **system prompt 附錄**，描述它 seed 了
  哪些起始檔案。Agent 的 prompt 是「template-無關的 base（`rca/prompts/system.md`）+ 該 profile 的
  `_prompt.md`」在 turn 時組起來的（`compose_system_prompt`），所以漏寫的話，agent 不會知道你 seed
  了哪些檔。它是 prompt metadata、**不會**被 seed 成 workspace 檔（`_walk` 自動跳過）。附錄只寫「本
  template 的起始檔 + 建議流程」；跨 template 的慣例（`/report.vN.md` 版本、`.canvas` schema、notebook
  由 user 執行）留在 base，不要重複。
- 其他副檔名：**原封不動**複製（notebook、`.canvas`、CSV…）。

範例 `my-profile/brief.md.tpl`：

```markdown
# ${title}

> 嚴重度 ${severity}．負責人 ${owner}

${description}
```

範例 `my-profile/_prompt.md`：

```markdown
## Your workspace — `my-profile` template

| Path | Purpose |
|---|---|
| `/brief.md` | One-page problem statement. Read first. |

Suggested flow: read `/brief.md` → … → draft `/report.v{N+1}.md`.
```

> `list_profiles()` 用「是不是資料夾」來判斷，所以 profile 名稱可以有連字號
> （如 `smt-reflow-example`）。

---

## 10. 改 Agent 的 System Prompt

RCA 的 system prompt 是純 markdown，存在
`src/workspace_app/rca/prompts/system.md`，由 `load_system_prompt()` 讀取。
**直接改這個檔**即可（不需重編譯）；或在你自己的 `AgentConfig` 用別的 prompt 字串。

> Prompt 裡描述的檔案慣例（如 `/report.vN.md`、`/data/*.csv`）要和你選的 workspace
> 範本一致，否則 agent 會引用到不存在的檔案。

---

## 11. 生產環境注意事項

- **對外服務**：`uvicorn.run(app, host="0.0.0.0", port=...)`。建議前面擺反向代理
  （TLS、驗證）；本應用本身沒有內建身份驗證。
- **持久化**：要重啟後資料還在，用 `SpecstarFileStore(spec)` 並用持久化的 spec；
  否則 `MemoryFileStore` 重啟即清空、`_seed_agent_configs` 會重種預設。
- **隔離**：`LocalProcessSandbox` 的絕對路徑解析與 host 隔離**需要 unprivileged user
  namespace**。容器化部署時，外層容器需允許 user namespace（或改用 `DockerSandbox`，
  或接受 `isolate=False` 的無隔離模式）。
- **逾時**：`exec_timeout`（單指令）與 `idle_timeout`（閒置回收 sandbox）依工作型態調整；
  RCA 預設較長（8 小時閒置）以支援「開著、晚點再回來」的調查流程。
- **LLM 連線**：用本機 Ollama 時確認 `ollama serve` 已啟動、模型已 `ollama pull`；
  用 hosted 時設好對應的 API key 環境變數。
- **Job runner ⊥ API（pod 切分，#312）**：背景 job（index 索引 / wiki 維護 /
  context-card 生成 / model-sanity）由 coordinator 在 specstar job queue 上消費。
  預設 `server.run_consumers: true` ⇒ **all-in-one**：API 進程自己也在進程內把全部
  consumer 起起來（本地開發 / 單 pod 最省事）。要讓 job runner 獨立 scale：

  - **API 設 `server.run_consumers: false`** ⇒ API 變**純 producer**：照常服務 HTTP
    + `enqueue`，但不消費任何 queue。
  - 每個 JobType 各跑一個 **worker 進程**，block-consume 自己那一種:

    ```bash
    python -m workspace_app.worker index      # 索引(chunk+embed,最吃資源)
    python -m workspace_app.worker wiki        # wiki 維護
    python -m workspace_app.worker card-gen    # context-card 生成
    python -m workspace_app.worker sanity      # model-sanity battery
    python -m workspace_app.worker eval        # 檢索品質 eval
    python -m workspace_app.worker graph       # knowledge graph 抽取
    python -m workspace_app.worker kb-import   # 知識庫封存包匯入(#715)
    ```

    這份清單是 `workspace_app.worker._JOBTYPE_ATTR` 的完整內容,而
    `kubernetes/base/workers.yaml` 每一種各有一個 Deployment ——
    `tests/deploy/test_worker_manifests.py` 會在兩邊對不上時失敗。**少一個
    Deployment 不會有任何錯誤**:那種工作照樣被接受、入列,然後沒有人做,
    對呼叫端而言和「佇列永遠不動」無法區分。

    一個 JobType 一個 Deployment ⇒ 各自掛 k8s HPA 獨立 autoscale，API 維持小。
    worker 收到 SIGTERM 會 drain 在途工作再退出（job 是 durable,硬殺也會被重投）。
  - **前提:共享後端**。in-memory 預設會讓每個 pod 各自一份 queue，worker 抓不到
    API 入列的 job — 真正切 pod 必須讓所有進程指向同一個 **Postgres** specstar
    後端（必要時 `message_queue.kind: rabbitmq`）。
  - 非 queue 的背景 sweeper（sandbox 閒置回收 / 鏡像 / 索引卡住回收 / blob-GC /
    code 同步 / **下班時間 goal 續跑（#615）**）**一律留在 API**，不受
    `run_consumers` 影響。下班 sweeper 特別留在 API 是因為它要起的是一個
    **turn** —— turn 需要 turn engine、sandbox 與 `ChatSendService`，那整套只
    存在於 API 進程；worker 沒有。多 pod 靠 specstar CAS 認領選出唯一一個 pod
    起跑，所以每個 replica 都跑這個 sweeper 是安全的。
    ⚠️**前提:API pod 半夜要活著**。若 HPA 夜間把 API 縮到 0,就沒有人掃描,
    過夜長跑不會發生（`kubectl get hpa` 確認 API 的 `minReplicas` ≥ 1）。
  - k8s 範例見 [`kubernetes/base/workers.yaml`](https://github.com/HYChou0515/ai-workspace/blob/master/kubernetes/base/workers.yaml)
    與 [`kubernetes/README.md`](https://github.com/HYChou0515/ai-workspace/blob/master/kubernetes/README.md)（每 JobType 一個
    Deployment + CPU HPA，sanity 固定 1 replica；不使用 KEDA）。
- **索引回填（#263，升級後一次性）**：本版替 `DocChunk` 加了 `provenance`
  位置索引（page / sheet / …，供「分析某檔第 N 頁」這類定位過濾），並替
  `SourceDoc` 加了 `path` 索引（檔名→文件解析），兩個 model 都升到 schema
  `v3`。specstar 在**寫入時**才抽取 `indexed_data`，不會自動回填舊資料，所以
  升級後**既有的 chunk / 文件查不到這些位置過濾**，直到 operator 跑一次遷移
  （它從已存的 `provenance` / `path` **重抽索引、不重新 parse 也不重算
  embedding**）：

  ```bash
  curl -X POST http://<host>/api/doc-chunk/migrate/execute
  curl -X POST http://<host>/api/source-doc/migrate/execute
  ```

  升 v3（而非沿用 v2）是因為生產資料多為 `None`、少數已是 `v2`；只在 v2 上加
  索引不會重抽那些已 v2 的列，跳 v3 才會讓**全部**列重抽。新寫入的列已直接帶
  索引，不需處理。
- **索引回填（`text` 三連字索引，升級後一次性）**：檢索不再整包載入整個
  collection，關鍵字（BM25）那半段改由 `DocChunk.text` 上的 pg_trgm 索引先縮小
  候選集，`DocChunk` 因此升到 schema `v6`。同樣地 specstar 只在**寫入時**抽取
  `indexed_data`，所以升級後**既有 chunk 的關鍵字檢索會查不到**（語意/向量檢索
  不受影響，新上傳的檔案立即正常），直到 operator 跑一次遷移 —— 它只從已存的
  `text` **重抽索引，不重新 parse 也不重算 embedding**：

  ```bash
  uv run python scripts/run_migrate.py --dry-run doc-chunk   # 先確認沒有 failed
  uv run python scripts/run_migrate.py doc-chunk             # 正式重寫
  ```

  pg_trgm 擴充與該 GIN 由 specstar 開機時自動確保存在，不需手動建。細節與
  回填前後的行為對照見 [資料遷移](migrations.md) §7。
- **索引回填（知識圖譜 reconcile，升級後一次性，不擋部署）**：每週的詞彙 pass
  以前要整張表撈回來才讀得到 mention 的 `surface` / `kind` / `occurrences`，
  以及 relationship 的 `predicate`、entity 的 `canonical_name`、link 的
  `proposed_from`。這六個欄位現在都建了索引，pass 因此只掃 metadata、完全不碰
  blob；四個 model 分別升到 `GraphMention v2`、`GraphEntity / GraphEntityLink /
  GraphRelationship v1`。

  ```bash
  uv run python scripts/run_migrate.py --dry-run graph-mention
  uv run python scripts/run_migrate.py graph-mention
  uv run python scripts/run_migrate.py graph-entity
  uv run python scripts/run_migrate.py graph-entity-link
  uv run python scripts/run_migrate.py graph-relationship
  ```

  **跟上面兩條不一樣的是:這次不跑也不會有錯的結果。** 讀取端發現某列的索引
  沒帶這些欄位時,會退回去讀它的 blob——因為把「沒有這個索引格」當成「名字是
  空字串」,會讓舊列被拿去當實體的顯示名稱,那是安靜的錯而不是大聲的失敗。
  所以遷移只是把那條退路關掉、換回全速,**部署順序不需要跟它對齊**。
- **索引回填(知識圖譜的比對鍵,升級後一次性,⚠️ 這一條會影響結果)**:`GraphClaim`
  升到 `v3`,它的 step **不是重抽索引,而是依當前規則重算比對鍵**
  (`norm_subject` / `norm_attribute` / `norm_period` / `norm_unit`)。

  ```bash
  uv run python scripts/run_migrate.py --dry-run graph-claim
  uv run python scripts/run_migrate.py graph-claim
  ```

  **上一條那句「不跑也不會有錯的結果」不適用於這一條。** 沒回填的列還帶著舊規則算出來
  的鍵,依現行規則本該視為同一件事的兩列可能還是兩件。好消息是每一列都記著產生它的
  schema 版本,所以「哪些還停在舊規則上」查得出來,不是猜的。細節見
  [資料遷移](migrations.md) §9。
- **索引回填(`workspace-file` 的 `path`,升級後一次性,🚨 不做的話 rollout 會停住)**:
  `WorkspaceFile` 升到 `v3`,把 `path` 加進索引讓 `ls(prefix=…)` 能下推。**這一條和上面
  每一條都不同 —— 它不是「變慢」或「少給答案」,而是會擋住部署。** 沒回填的列答不出
  `path` 述詞,檔案樹和每一份 entity 列表都會在資料完好的情況下讀成**空的**;所以
  `/api/readyz` 在回填完成前一律回 **503**,而 k8s 的 readinessProbe 就指著它 ——
  **新 pod 永遠不會 ready,rollout 停在那裡**(liveness 故意走靜態路由,讓那些 pod 活著
  給你用)。

  ⚠️ **回填不能打 Service。** Service 只導流量給 ready 的 pod,而卡住的時候 ready 的
  全是**舊 pod**;migrate 只會把每列帶到「該 pod 認得的最新版」= `v2`,而 `v2` 沒有
  `path`。打在 Service 上會**回報一整排成功、什麼都沒改**。要直接連一個新 pod:

  ```bash
  kubectl get pods -l app=rca-app                      # 找一個新的、還沒 ready 的
  kubectl port-forward pod/<新 pod 名稱> 8000:8000      # 不經過 Service,不 ready 也連得到

  uv run python scripts/run_migrate.py --dry-run --base-url http://localhost:8000 workspace-file
  uv run python scripts/run_migrate.py           --base-url http://localhost:8000 workspace-file

  curl -i http://localhost:8000/api/readyz             # 200 "ok" = 好了,新 pod 會自己 ready
  ```

  順序是**先 rollout、再回填**:新 pod 起來但不 ready 是預期的,舊 pod 繼續服務,沒有
  中斷。全新安裝不受影響(沒有舊列時 `readyz` 一開始就是綠的)。完整說明見
  [資料遷移](migrations.md) §8。

---

## 15. 第三方工具（#674）：上架、換版、退回

外部團隊寫的工具不進我們的 repo。他們在自己的 CI 用我們的 builder image build，
把 artifact 網址交給我們；**新的 sandbox 啟動時自動帶上**。作者面的說明在
[寫一支工具（外部作者）](tool-authoring.md)，這裡只講我們要做的事。

### 15.1 一次性設定（**不是**每支工具都要做）

下面這些做一次就好。做完之前，第一支第三方工具會被擋下來——訊息都寫得出原因，但先知道順序
可以省一趟。

| 做什麼 | 多常做 | 沒做會怎樣 |
|---|---|---|
| `TOOL_BUILDER_ID` — 給 sandbox-host、tool-builder、mcp-runner **同一個值** | 每次發版 | **整個第三方功能關閉**。沒有 ABI 錨就沒得比對，而不能比對就不該去抓——會掛上一個為別的底層 build 的 bundle，然後在使用者面前壞掉 |
| `TOOL_ARTIFACT_HOSTS` — 憑證能被送到哪些網域（逗號分隔），給 sandbox-host 與 mcp-runner 映像 | 一次（換 artifact store 才改） | 憑證**永遠不會被送出**，private 的 GitLab project 抓不到。這是刻意的預設，理由見 §15.8 |
| `TOOL_ARTIFACT_TOKEN` — 讀 artifact 用 | 一次 | private 的抓不到（public 仍可）。**只有 host 需要**，app 從不持有 |
| `TOOL_ARTIFACT_INSECURE_TLS` — 不檢查 artifact store 的 TLS 憑證 | 只在必要時 | 預設是**檢查**。內部 store 沒有可交給部署的 CA 時才設；代價見下方 |
| `grant keygen --as <代號>` → 公鑰進 `TRUSTED_KEYS` → 發版（§15.7） | **每人**一次 | `TRUSTED_KEYS` 是空的 ⇒ 任何憑證都驗不過 ⇒ **一支第三方工具都上不了架** |
| 發布 mcp-runner 映像（§15.8） | 每次發版 | 工程師沒辦法在自己的編輯器裡用這些工具。平台本身不受影響 |

`TOOL_BUILDER_ID` 三個映像必須同一個值。不同步正是那道閘門存在的理由——它會擋下來，
而不是讓它在執行期壞掉；有測試釘住三顆映像都帶這個旋鈕。

**`TOOL_ARTIFACT_INSECURE_TLS`（`1`／`true`／`yes`）關掉憑證檢查**，任何憑證都接受：未知簽發者、
自簽、過期、主機名不符。它存在是因為內部的 artifact store 常常沒有一份可以交給部署的 CA，
而唯一的替代方案（`SSL_CERT_FILE` 指向那份 CA）在沒有 CA 時無路可走。

代價要寫在旋鈕旁邊，不是寫在 review 意見裡：**這條路抓回來的是會被解開並執行的程式碼**，
而另外兩個錨點都補不上這個洞——`bundle.sha256` 跟 manifest 同源，能換一個就能換另一個；
憑證綁的是「名字 + 網址前綴」，而攔截者用的正是同一個網址。TLS 是這裡唯一把位元組和
「發布它的那台主機」綁在一起的東西。開啟時 host 會在 log 留一行 warning 指名這個變數。

沒有這個需求的部署不要設它。它是 runtime 變數，四個執行體裡只有會自己去抓 artifact 的那三個
（sandbox-host、mcp-runner、operator 跑 `verify` 的 shell）需要；`build-tool` 和 app/API pods
一個都不用——app 從不直連 artifact store。

**每支工具要做的**是另一回事，而且只有兩件：發一張憑證（§15.7），以及把名字和網址寫進
`app.json` 再發版（§15.2）。

### 15.2 上架一支新工具

先發憑證（§15.7）再驗再登記。**憑證是作者 build 當下凍進 manifest 的**（`manifest.grant`），
不是你事後掛上去的——所以一份在拿到憑證**之前**就做好的 artifact，不管網址多正確都會被拒絕，
作者必須提交 `tool-certificate.token` 之後**重跑一次 CI**。順序錯了要重做的是他們那一趟。

#### 第 1 步：驗

```sh
TOOL_BUILDER_ID=<這個部署的值> \
TOOL_ARTIFACT_TOKEN=<你的 token> \
TOOL_ARTIFACT_HOSTS=gitlab.example \
  uv run python -m workspace_app.tooling.verify \
    'https://gitlab.example/api/v4/projects/7/jobs/artifacts/main/raw/dist/tool.manifest.json?job=build-tool' \
    --name wafer-history
```

不會執行對方的程式碼，只做抓取 + 閘門 + 結構比對。跑的是 host 每次 resolve 用的**同一組**
`check_compatible` + `admit`，所以 exit 0 就代表正式環境收得下。過了會印：

```
accepted: wafer-history 1.4.2 (trend, compare) sha256=… , size granted by hychou
```

**括號裡那串是這支工具的 command 名單**，第 2 步會用到——那是唯一的來源，見下面「只給部分
command」。

`TOOL_ARTIFACT_HOSTS` 要一起帶：沒帶就不送 token，private 專案回的是 **404 而不是 403**，
和「網址打錯」長得一模一樣（§15.9）。

#### 第 2 步：寫進 `app.json` —— **兩個欄位都要**

```json
"agent": {
  "tools": ["…", "wafer-history"],
  "external_tools": { "wafer-history": "<第 1 步驗過的同一個網址>" }
}
```

兩個欄位回答**不同的問題**，而且少寫哪一半都**不會有任何錯誤訊息**：

| 欄位 | 回答什麼 | 只寫這個會怎樣 |
|---|---|---|
| `external_tools` | bytes 從哪來 | 這支工具根本不會被 resolve；`tools` 裡那個名字對不到任何套件，靜默跳過 |
| `tools` | 誰可以用它 | host 照抓、照驗憑證、照解壓掛載，但選 command 的那一步選不到它 → **模型一個 command 都拿不到，而且哪裡都不會抱怨** |

`external_tools` 的 key **不可以含冒號**——那個字串同時是憑證比對用的 `tool`，也是 sandbox 裡
`../.tools/<名字>` 的路徑。

#### 只給部分 command

和第一方套件同一套 colon 語法，而且寫在 **`tools`**（`external_tools` 沒有放它的位置）：

```json
"agent": {
  "tools": ["wafer-history:trend"],
  "external_tools": { "wafer-history": "<網址>" }
}
```

第一方和第三方走的是**同一條**選取邏輯——一個 turn 會把開機掃到的套件和這次 resolve 到的
第三方套件併成同一個 list 才交出去，所以它根本不分內外。

再收窄還有兩層，用的是同一組字串：profile 的 `_profile.json`，以及 per-item 的工具挑選。

> ⚠️ **command 名字打錯是靜默跳過**——只留一行 debug log，不會啟動失敗、UI 也不會少一塊，
> 症狀就只是「那個工具沒出現」。第一方至少還能翻 `.workspace-tools/<名字>/commands.json`；
> 第三方**沒有本機檔案可翻**，名單只有第 1 步 `verify` 輸出括號裡那一串。別用猜的。

> ⚠️ 挑 command 是**我們這端**做的，host 不知道你只要其中兩個：bundle 一樣整包下載、整包掛載。
> colon 語法省的是模型的 context，不是磁碟。

#### 名字要一致的三個地方

**名字是我們定的**，作者取什麼完全不參與（所以兩個作者都叫 `data-fetch` 也不會互相蓋掉，
各發一張憑證、各取一個名字）。要一致的是這三個，其他都不必：

1. 憑證的 `--tool`（§15.7）
2. `app.json` 裡 `external_tools` 的 key
3. `app.json` 裡 `tools` 的那一項（bare，或 `<key>:cmd` 的前半）

作者 `pyproject` 的 `name`、`[project.scripts]` 的進入點名稱**刻意不要求**跟上面一致——
體積檢查和簽發者查詢都是不帶名字做的，綁了名字反而會出現「在一道閘門過、在下一道被拒」。

#### 這支工具需要哪些環境變數(#750)

工具作者可以在 bundle 裡放一份 `env.json`,說出這支工具需要哪些變數。**你這端不用做任何事**
就會生效:使用者打開環境變數視窗時,上面會多出一份對照表,標出哪些還沒填 ——
在 #750 之前那裡是**沒有對照表的自由填空**,使用者只能跑下去看它爆。

三態要分清楚,UI 一路守著它:

| bundle 裡的 `env` | 意思 |
| --- | --- |
| **鍵不存在** | 作者**沒講**(#750 之前發布的 artifact 全是這樣)——不等於不需要 |
| **空陣列** | 作者**看過而且不需要** |
| 有內容 | 這些是它要的;`required` 只是標示,**不擋執行** |

**這是便民工具,不是閘門。** 平台不會因為變數沒填就拒絕跑那支工具 —— 沒宣告的工具照舊能跑,
`required` 也不擋。作者宣告格式錯的話,**錯在作者自己的 prebuild**(當場失敗並指名檔案),
不會變成你的問題。

#### 讓使用者用帳號密碼換出變數(#750,選用)

有些變數其實是 token,叫使用者「去某某系統撈一個貼過來」很不實際。你可以掛自己的登入實作:

```yaml
server:
  env_providers:
    - "mycorp.plugins.SapLogin"      # 你寫的 IEnvProvider
```

實作長怎樣見[擴充平台](extending-the-platform.md)。掛上之後,環境變數視窗會出現登入表單;
**換出來的值是填進表單、不自動存**,帳號密碼本身不落地。**沒設 = 完全沒有這顆按鈕**,
每個變數仍然可以手動填——那條路永遠有效。

⚠️ **工具從不指名要用哪個登入方法,平台是用「變數名字」比對決定給不給那顆按鈕。** 這是刻意的:
讓第三方指名方法,等於讓它決定你的 UI 向使用者要哪一組憑證,而那個畫面帶著的是**你的**可信度。
撞名的落點會是登入視窗——A 廠的密碼打進 B 廠的表單,全程不報錯。

⚠️ **信任邊界有個既有的洞,這個功能沒有讓它變寬,但你要知道**:密碼只有第二方實作看得到,
可是**換出來的值**會跟著整份 `user_env` 交給**每一支**工具。所以這裡不做許可清單——擋了按鈕
沒擋值,只會讓人以為擋住了。

### 15.3 一支工具什麼時候真的進到 sandbox 裡

一句話決定了下面所有的行為：**掛載只發生在 `create` 那一刻，之後永不重建。**

它分成兩段，發生在不同機器上，而且**只有第二段**會讓工具真的能被執行：

| | 誰做 | 何時 | 結果 |
|---|---|---|---|
| **resolve** | app 問 host `POST /tools/resolve` | 每個 turn 開頭；另外開機時預熱一次 | 驗 builder/憑證、抓 bundle、解壓進 host 的 `ext/<sha>` 快取。**沒有任何 sandbox 因此拿到工具** |
| **mount** | host 在 `create` 裡 | 建立一顆 sandbox 時，**僅此一次** | 對這次 spec 帶的每個 `{名字: sha}` 建一條 `.tools/<名字> → ext/<sha>` |

所以「工具在快取裡」和「工具在某顆 sandbox 裡」是兩件事，前者不蘊含後者。

**誰會建 sandbox**：agent turn、使用者手動開的終端機（`POST …/exec`）、workflow 的
deterministic node、以及檔案操作撞到 sandbox 不見時的復原。四條路建出來的都是完整的
sandbox，所以**任何一條都必須把工具帶上** —— 它們掛的是同一份東西：這個 item 的 App 在
`app.json` 宣告的 `external_tools`。turn 唯一特別的地方是它剛好已經 resolve 過，所以用
自己那次的 sha（好讓模型看到的 schema 和跑的 bundle 是同一份）。

這條規則被違反過一次，症狀值得記住：只有 turn 帶了工具，於是**重新部署後誰先叫醒 sandbox，
誰就決定了那顆 sandbox 一輩子有沒有第三方工具**——先開終端機再講話，工具就整個不見。

**一顆活著的 sandbox 不會被追加工具。** 它的 `.tools` 是建立當下的快照，工具的加入、換版、
移除都只影響**之後**建立的 sandbox。所以：

- 剛註冊一支新工具 → 對既有 sandbox 無效，要等它被回收重建
- 一個 turn 若發現它需要的 bundle 不在這顆 sandbox 裡，會把那支工具報成不可用**並附上原因**，
  而不是交給模型一個不存在的啟動器（否則症狀是 `No such file or directory`，既不講工具名
  也不講原因）

### 15.4 換版本：不用做任何事

網址指的是「最新的 artifact」，作者 push 完，**下一個開起來的 sandbox 就是新版**。
不用改 repo、不用重新部署。

**活著的 sandbox 會繼續跑舊版**，這是刻意的：作者例行發版不該把正在用的人手上的工具抽走。
代價是那顆 sandbox 的餘生裡，模型看到的 schema 可能比實際執行的 bundle 新一版。

### 15.5 退回某一版

把 `external_tools` 的網址從「最新」改成**指定那次 build** 的 artifact
（GitLab 的 `/jobs/<job_id>/artifacts/…`），然後發版。sha 若還在該 host 的快取裡是秒回，
被回收了就重抓一次，同一條路。

> 「跟著最新」和「釘死某版」是**同一個欄位的兩種寫法**，沒有第二套機制。

### 15.6 磁碟

每支 bundle 約 150MB，而且**新舊版本會並存**（舊的留著，回滾才會是重掛而不是重抓）。
用 `SANDBOX_HOST_TOOL_CACHE_MAX_BYTES` 設上限：超過就由舊到新淘汰，
但**正在被使用的永遠不會被淘汰**。粗估：`工具數 × 保留版本數 × 150MB × host 數`。
**沒設上限 = 不淘汰任何東西**（和這個 repo 其他限制的慣例一致:未設即無限)。要讓 reaper
真的回收磁碟,就給一個數字——那時才會由舊到新淘汰沒被引用的版本。

### 15.7 工具憑證(准入 + 體積)

**每一支第三方工具都需要一張你簽的憑證。** 把網址貼進 `app.json` 不算核准——憑證才算。
沒有憑證的 artifact，host 每次 resolve 都會拒絕。

一張憑證講三件事：

| 欄位 | 意思 | 誰檢查 |
|---|---|---|
| `tool` | **你**給它的名字，就是身分 | host 每次 resolve、上架時的 `verify` |
| `source` | 它的 artifact 住在哪（網址前綴） | 同上 |
| `max_bytes` + `publish_until` | 能多大、以及**發布期限** | 只有發布端（作者 build、你的 `verify`）|

**發布期限不會讓工具停掉。** 它是給作者的：「你今天急著上，體積先放你過，但這個日期前要處理好。」
過期之後他發不出新的超標版本，**已經在跑的那一版原封不動**——拖延是他的事，使用的人沒有同意
要跟著受罰。所以 host 根本不讀這個欄位。

因為身分來自憑證而不是作者取的名字，**兩個團隊的工具都叫 `data-fetch` 也不衝突**，各拿各的
id 就好。

#### 開通（每人只做一次）

**一人一把金鑰。** 每個要能發憑證的人各跑一次，`--as` 填自己的代號。在這個 repo 的 checkout 裡跑：

```bash
uv run python -m workspace_app.tooling.grant keygen \
    --key ~/.secrets/tool-grant.pem --as hychou
```

私鑰只寫到你指定的路徑（`0600`，已存在就拒絕覆寫——覆寫等於讓你已發出的憑證全部失效）。
指令印出一行，加進 `src/workspace_app/tooling/grant.py` 的 `TRUSTED_KEYS`：

```python
TRUSTED_KEYS: dict[str, str] = {
    "hychou": "ByiVvmDZtAhssyCIikYGsWWyL81PmYW/jFLcKsbdRgI=",
}
```

**發版之後才生效。** 在那之前 `TRUSTED_KEYS` 是空的，任何憑證都驗不過，也就是**沒有任何
第三方工具能上架**。

**為什麼是一人一把，而不是一把共用金鑰加一個「發證者」欄位：** 欄位是自己填的，值多少就看
填的人多誠實，而且真的出事那天它會和簽章各說各話。金鑰不會——只有本人有那把私鑰。

它同時是**關掉的開關**：有人離職，把他那行拿掉並發版，他核准過的工具全部立刻停用。

代價要知道：**新增或移除一個發證者是改 code + 發一次版。**

#### 發一張憑證

**第 1 步：決定名字。** 這是**你**取的，會成為 `app.json` 裡 `external_tools` 的 key，也是
模型看到的名字。和作者的 command 叫什麼無關。

**第 2 步：確認名字沒被用過。** `issue` 會擋，但你可以先看
[`tool-registry.csv`](https://github.com/HYChou0515/ai-workspace/blob/master/tool-registry.csv)。
同一個名字發兩張，那兩張就互相通用——而憑證是公開的，對方 manifest 裡看得到。

**第 3 步：找出他的 artifact 住在哪。** `--source` 是**前綴**，不是某一個 artifact：

```
https://gitlab.example/api/v4/projects/rca%2Fwafer-history/
```

貼整串 manifest 網址會被擋——那樣**回滾當天會擋住你自己的修復動作**（回滾是指向某次 build 的
artifact，網址不同）。只給網域也會被擋（那台 GitLab 上任何專案都能冒用這個名字）。

**第 4 步：簽。**

```bash
# 一般情況
uv run python -m workspace_app.tooling.grant issue \
    --tool wafer-data-fetch \
    --source https://gitlab.example/api/v4/projects/rca%2Fwafer-history/ \
    --key ~/.secrets/tool-grant.pem

# 放寬體積，並給他一個月處理
uv run python -m workspace_app.tooling.grant issue \
    --tool pdf-extract --max-mb 300 --publish-until 2026-09-01 \
    --source https://gitlab.example/api/v4/projects/docs%2Fpdf-extract/ \
    --key ~/.secrets/tool-grant.pem
```

`--max-mb` 一定要配 `--publish-until`——沒有期限的放寬只是「某支工具的上限比較大」，
不會有人再回頭看。

**第 5 步：回信 + 記錄。** stdout 那一行給對方，請他：

> 存成 repo 根目錄的 `tool-certificate.token`（整行，不要換行）並提交。下次 build 就會生效。

stderr 會印出要加進 `tool-registry.csv` 的那一列，把 `<their repo>` 之類補上，和登記工具的那次
改動一起送。

**第 6 步：登記 + 確認。** 照 §15.2 把名字和網址寫進 `app.json`，發版，然後跑 `verify`：

```
accepted: wafer-data-fetch 1.4.2 (trend) sha256=… , size granted by hychou
```

#### 有人離職，或要換金鑰

把他那一行從 `TRUSTED_KEYS` 拿掉並發版。**他核准過的工具立刻全部停用**——署名和撤銷是同一個
機制。換金鑰同理：新舊並存，用新的簽，舊的留到它最後一張憑證不再需要為止。

#### 一件必須知道的事

**憑證發出去之後你改不到它。** 對方是離線驗章的。所以：

- 要下架**單一一支**工具 → 從 `app.json` 拿掉並發版
- 要一次停掉**某人核准過的全部** → 從 `TRUSTED_KEYS` 拿掉他的金鑰並發版

### 15.8 讓工程師用自己的 agent 跑同一支工具（MCP runner）

同一份 artifact，除了平台會拉，也可以被工程師自己的 agent（Claude Code／opencode／codex）
透過 MCP 呼叫。**一顆 runner image 對應所有工具**，工具靠網址帶進來。

```bash
docker build -f sandbox-host/mcp-runner.Dockerfile \
    --build-arg BUILDER_ID="$THE_SAME_ID_YOU_GIVE_TOOL_BUILDER" \
    --build-arg ARTIFACT_HOSTS=gitlab.example \
    -t registry/ai-workspace/mcp-runner:<tag> .
```

`ARTIFACT_HOSTS` 是**憑證能被送去的網域**（逗號分隔）。host 映像要給同一份——它列在 §15.1 的一次性設定裡。

沒設的話 token 永遠不會被送出去——聽起來很嚴格，但反過來是災難:runner 抓 manifest 是發生在
驗證**之前**的，所以只要有人讓工程師執行一個惡意網址，他的 GitLab token 就會被送過去，
而他看到的只是一句「安裝失敗」。憑證擋得住那份程式碼,擋不住那個 token——順序不對。

`BUILDER_ID` 要和你給 `tool-builder`、`sandbox-host` 的**同一個值**——runner 會直接執行
第三方 bundle，所以它跟 host 受同一條 ABI 規則約束。有測試釘住這三顆映像的錨點一致。

**build context 必須是 repo 根目錄**（上面那行結尾的 `.`）。三個 Dockerfile 的 `COPY` 路徑都寫成
`sandbox-host/…`，用 `sandbox-host/` 當 context 會直接 `COPY failed` ——會明確失敗，不會產生
一顆壞掉的映像，但共用的 CI build template 若預設拿 Dockerfile 所在目錄當 context 就要改。

映像刻意不依賴 PATH 或某一種安裝方式：entry point 是 venv 直譯器的**絕對路徑**，而 `src/`
同時掛在 `PYTHONPATH` 上。三種情況都會產生同一句 `No module named sandbox_host`——外面的東西
插進 PATH 前面、`uv sync` 的 editable 安裝斷了連結、或某棵樹設了 `[tool.uv] package = false`
（uv 稱之為 virtual project，完全不安裝）。一句話三個成因，所以兩邊都釘死，有測試守著。

#### 怎麼交到工程師手上

**不要把下面那段設定貼給他們。** 他手上通常只有工具的 GitLab repo 網址，而要湊出一個能用的
設定還缺工具名稱、artifact 網址、runner image 和一串 docker 參數——那是四件他沒理由知道的事。

改成發 `tool-skill/` 裡的 skill：把 `SKILL.md` 的 `<<RUNNER_IMAGE>>` 換成你發布的 image
位址，其餘不用動。他裝好 skill、把 repo 網址丟給自己的 agent，agent 就會去讀 repo、推出
artifact 網址、寫設定、然後跑一次確認。

skill 花了不少篇幅在講**失敗怎麼辦**，因為照著做的人是一個人，而且失敗會落在三個不同的人身上
（作者／平台團隊／他自己）。細節見 `tool-skill/README.md`。

下面這段是它會寫出來的東西，列在這裡供你排查用：

工程師那邊一支工具一筆設定，差別只有最後那個網址:

```json
{ "mcpServers": { "wafer-history": { "command": "docker", "args": [
    "run","-i","--rm",
    "-v","mcp-tools:/cache","-v","${PWD}:/work",
    "-e","TOOL_ARTIFACT_TOKEN",
    "registry/ai-workspace/mcp-runner:<tag>",
    "wafer-history","https://gitlab.example/.../tool.manifest.json" ] } } }
```

**不同的 client 設定格式不一樣。** 上面那份是 `mcpServers` 家族（Claude 系）。opencode 用
`mcp` + `type: local`，而且**抓工具清單的預設逾時只有 5 秒**——第一次啟動要下載整包 bundle，
一定超過，症狀是 client 顯示 loading 然後失敗，不會說是逾時:

```jsonc
{ "$schema": "https://opencode.ai/config.json",
  "mcp": { "wafer-history": {
    "type": "local",
    "command": ["docker","run","-i","--rm",
      "-v","mcp-tools:/cache","-v","/absolute/path/to/project:/work",
      "-e","TOOL_ARTIFACT_TOKEN",
      "registry/ai-workspace/mcp-runner:<tag>",
      "wafer-history","https://gitlab.example/.../tool.manifest.json"],
    "enabled": true,
    "timeout": 180000 } } }
```

opencode 的變數替換是 `{env:VAR}`（**不是** `${PWD}`），而且變數沒設時會替換成空字串——
`-v :/work` 會失敗。掛載路徑用絕對路徑最不會出事。

幾件值得知道的:

- **bundle 不會被存第二份。** artifact store 裡已經有一份，runner 依 sha 存進
  `mcp-tools` volume，第二次啟動就命中。以前的做法是每支工具烤一顆 image，等於把同樣的
  位元組再存一遍（每支 × 每版）。
- **撤銷靠 artifact 的讀取權,而且只在 runner 這一側成立。** host 抓不到 artifact 時會用
  上次成功的版本繼續服務（一個外部故障不該讓所有 workspace 停擺）;runner **刻意不這樣做**
  ——確認不到就不跑。拿掉某人對該工具 artifact 的讀取權,他下次啟動就用不了,這是唯一
  一個對「已經發出去的本機設定」還有效的控制點。代價是 GitLab 不通時他的工具也不能用。
  （不能靠狀態碼分辨「被撤銷」和「artifact 過期」:GitLab 對看不到的私有專案一樣回 404。）
- **快取是可選的。** 不掛 `/cache` 就每次啟動重抓一次，一樣能跑，機器上不留東西。
  掛與不掛的差別是磁碟換頻寬,**不是新舊**——兩種模式每次啟動都會問一次 manifest。
  映像刻意不宣告 `VOLUME /cache`:那會讓沒掛載的每一次執行都拿到一個匿名 volume，
  只有 `--rm` 會清掉,其餘情況每跑一次就留一個裝著整份解開 bundle 的孤兒。
- **它跑的是和平台同一段 `resolve`。** 同樣的 builder 閘門、同樣的 sha 驗證、同樣的
  「artifact 過期」提示。烤進 image 的做法在執行時**什麼都不驗**——複製進去的是什麼就跑什麼。
- **新版自動生效**,和「下一個 sandbox 就是新版」同一個性質。
- **設定裡沒有機器相關的東西,同一份可以發給所有人。** runner 會自己降權成 `/work` 的
  擁有者,工具產出的檔案就歸使用者所有。判斷依據是「行程 uid vs `/work` 目錄的擁有者」,
  不是「是不是 root」——rootless docker（行程是 root 但檔案本來就落在使用者名下）不會被
  誤降。映像裡的 `/cache` 是 0777,就是為了讓降權後仍寫得進去。
- **沒掛 `/work` 的話,寫檔是靜默丟失。** 讀檔會大聲失敗,寫檔卻會「成功」然後隨容器消失。
  runner 啟動時會在 stderr 提醒。
- **快取 volume 請一個人用一個**，而且映像裡的 `/cache` 是 **1777**(sticky)不是 0777。
  在 Unix 上，能不能刪掉／改名一個項目看的是**父目錄**的寫入權，不是那個項目的擁有者——
  所以少了 sticky，一支工具可以把另一支工具的 `/cache/<sha>` 整個換掉，而 `ensure` 命中
  時不會重讀 bytes，下次就直接執行被換掉的東西。降權只是把手法從「覆寫檔案」變成
  「替換目錄項目」;sticky 才是關掉它的東西(那也是 `/tmp` 用它的原因)。
  host 端不走這條:那裡是多個不同 uid 的 sandbox 共用一棵樹，所以整棵 chown 成 root。
- **叫他們用 named volume，不要 bind mount 主機目錄。** 容器以 root 執行（和 host 一樣），
  bind mount 的快取會變成 root 所有，使用者之後刪不掉;named volume 用
  `docker volume rm` 就清得掉。

### 15.9 出事時怎麼查

- **某支工具突然不見**：agent 的 prompt 裡會有一段「Tools that are unavailable right now」
  寫著原因。最常見的是 GitLab artifact 過期（作者的 CI 沒設 `expire_in: never`）。
- **工具在跑但行為怪怪的**：resolve 的紀錄有 `name → sha + version`，可以確認那個 turn
  用的是哪一版。
- **artifact store 連不上**：host 會用**上次成功**的版本繼續服務並標記 `stale`，
  不會讓 workspace 開不起來。
- **`cannot resolve <名字>: …`**：這句只在**抓 manifest 失敗**時出現，所以憑證、ABI、sha
  都還沒輪到——是網路、TLS 或 token。冒號後面那半才是原因，別只看前半。
  `is unreachable:` 涵蓋 HTTP 錯誤（`HTTPError` 是 `OSError` 的子類），所以 404 也長這樣。
- **`verify` 說 404，但同一個網址 `curl` 得到 200**：`curl` 無條件送 token，`verify` 只在
  hostname 出現在 `TOOL_ARTIFACT_HOSTS` 時才送。GitLab 對看不到的私有專案回 **404 而非 403**，
  所以「沒帶 token」和「網址錯」長得一模一樣。
- **`No module named sandbox_host`**：映像的問題，不是 artifact 的問題。三個成因見 §15.8。
- **MCP client 一直 loading 然後失敗**：多半是 client 端的逾時（opencode 預設 5 秒），
  不是 server 壞掉。先在終端機直接跑設定裡那串 `docker run`，stderr 第一行會說實話——
  client 通常把它吃掉了。

---

### 上下文窗口與自動壓縮:誰決定、怎麼確認、什麼時候才需要你出手

一個對話能塞多少、什麼時候會自動壓縮,全部由**端點的 context 窗口**決定。這個數字
**不是設定出來的,是解出來的** —— 依序問五個地方,第一個答得出來的就算:

| 順位 | 來源 | 適用 |
|---|---|---|
| 1 | `history.context_limit`(你設的) | 逃生口,永遠最大 |
| 2 | 從流量學到的 | 端點拒絕時明講的上限;或它回報讀到的 token 遠低於我們估的(⇒ 靜默截斷) |
| 3 | **問端點**:vLLM 的 `POST /tokenize` → `max_model_len` | 自架 vLLM。每個端點只問一次 |
| 4 | 模型登錄檔(litellm) | hosted 模型、`ollama/*` |
| 5 | 都答不出來 ⇒ `unknown` | 自架模型掛在 OpenAI 相容端點、又不是 vLLM |

#### `unknown` 的後果 —— 最容易被誤解的一點

解不出上限時:

- 歷史**完全不裁切**(寧可全送,也不照猜的數字截肢);
- **自動壓縮完全不會執行**。壓縮的觸發條件就是「裁切器即將開始丟東西」,沒有上限就沒有
  那個時刻;
- 手動那顆「壓縮」按鈕**仍然可以按**,它刻意跳過預算檢查(按的人有我們從 token 數看不到
  的理由)。

所以:**看不到任何自動壓縮,不代表壞掉,可能只是沒有上限可依據。** 這兩種狀態在畫面上
長得一樣,下面兩個檢查可以分辨。

#### 怎麼確認你是哪一種

**一、看 log**(自架 vLLM 最快的一條):

```bash
grep "context probe" <backend log>
```

- `context probe: …/tokenize reports max_model_len=N` → **解出來了**,自動壓縮以 N 為準。
- 沒有這行 → 探測沒有答案。這是**正常路徑**,不是錯誤:`/tokenize` 是 vLLM 的擴充,
  不在 OpenAI 相容規格裡,Ollama 沒有這個端點。接著會往登錄檔找。

> ⚠️ **自架 vLLM 但前面擺了 litellm proxy 的話,這個探測多半答不出來。** 探測打的是
> `sandbox` 之外的那個 `base_url` —— 也就是 proxy —— 而 `/tokenize` 是 vLLM 的擴充,
> proxy 沒有這條路由,回 404,探測就放棄(失敗只寫 debug,所以 log 上什麼都看不到)。
> 於是「用了 vLLM」和「拿得到 max_model_len」**不是同一件事**,這是最容易誤判的一組。

**二、看 API**(任何部署都適用):

```
GET /a/{slug}/items/{item_id}/chats/{chat_id}/context
```

回的 `limit`:

- 有數字 → 自動壓縮在運作,門檻就是它;
- `null` → **沒有上限,自動壓縮不會動**。

同一份回應裡的 `measured` 是另一件事:`true` 代表這個用量數字是 provider 自己回報的,
`false` 代表是我們估的。估法是 `CJK 字元數 + 其餘字元數 ÷ 4`,對中文特別重要,但終究是估。
要讓它變成實測,需要在該 preset 上宣告 `reports_usage: true` —— **判定程序(三個 curl,
含「為什麼兩個不夠」)寫在 `configs/config.example.yaml`,不在這裡重複**。

> ⚠️ 剛壓縮完的那一刻,`measured` 會退回 `false`:壓縮前那個實測值是「還包含被摘要掉那段」
> 的請求回報的,留著會讓數字卡在舊值,所以錨點被刻意丟掉。下一輪回答拿到新的實測值之後
> 會再跳上來 —— 那不是對話又變長了,是量尺換回含系統提示與工具說明的那一把。這一段值得
> 照抄給使用者,否則那個跳動看起來就像 bug。

#### 什麼時候才需要你出手

**只有上面兩個檢查都指向「沒有」時**,才需要自己設:

```yaml
history:
  context_limit: 32768        # 端點真正的窗口,例如 vLLM 的 --max-model-len
```

設之前務必**確認數字是對的**。設太大 → 請求被端點拒絕(會被記下來並修正,但每次都白跑
一趟);設太小 → 對話被無謂地砍短,而且沒有任何錯誤訊息。不確定就**不要設**,讓它維持
`unknown`:全部送出去、從端點的回應學,比照一個沒人量過的數字截肢安全。

> 這裡刻意不列預設值。`unknown` 就是 `unknown`,不會偷偷代入一個數字 —— 曾經有一個
> 24,000 的常數在治理一個沒人量過的窗口,那正是這套階梯要取代的東西。

#### `unknown` 到底會不會出事

會,但形狀取決於端點怎麼處理過長的請求:

- **端點會拒絕,而且訊息裡講出上限**:第一個超長的回合撞牆,那個數字被記下來,之後就
  正常了。已在一個真實部署上量到:自架 vLLM 前面擺 litellm proxy,拒絕訊息**確實帶著
  上限**,解析得出來。代價是那一趟 —— 而且 `LimitLearner` 是**每個 pod 各自記在記憶體
  裡**,所以每次重啟、每次擴容出新 pod,都會再賠一次。
- ⚠️ **端點會拒絕,但訊息裡沒有數字**:這一種**學不到任何東西**,所以不是「撞一次就好」,
  而是**每次都撞**。中介層會改寫請求的部署尤其要當心 —— 同一個 proxy 會把
  `max_tokens: 999999999` 靜靜吃掉而不是拒絕,能改寫請求的也能改寫錯誤。
  **這種拓撲下 `context_limit` 不是優化,是唯一能讓那個數字到位的途徑。**
- **端點會靜默截斷**(Ollama 的常態):沒有錯誤、沒有警告,模型從一段它其實沒看完的
  prompt 流暢地作答。這個失敗**只有一個訊號**——provider 回報它實際讀了多少 token——
  而串流路徑要拿到那個數字,需要該 preset 宣告 `reports_usage: true`。**沒宣告的話,
  三道防線一道都不會啟動。**

所以先確認你是哪一種,再決定要不要設 —— **而「會拒絕」不等於「學得到」**,要看訊息裡有
沒有數字。一段可以直接跑的判別指令在下面。

> ⚠️ **跑之前先確認模型是活的。** 端點掛掉時這支探測會回一個沒有上限的錯誤,長得跟
> 「拒絕但不講數字」一模一樣 —— 一個停機中的服務會給你一個看似合法的否定答案。先送一
> 個正常的短請求確認會回話,再跑這個。

> 用越來越長的 prompt 送過去,第一個非 200 就停(所以多數情況第一輪就結束,不會白送幾 MB)。
> 把 `BASE` / `KEY` / `MODEL` 換成該 preset 的 `llm.base_url` / `llm.api_key` / `model`
> (去掉 `openai/` 前綴)。

```bash
BASE='http://your-litellm-proxy:4000/v1' KEY='sk-...' MODEL='your-model-name' \
python3 - <<'PY'
import json,os,re,urllib.error,urllib.request
BASE,KEY,MODEL=os.environ['BASE'].rstrip('/'),os.environ['KEY'],os.environ['MODEL']
F='這是一段用來測試上限的文字。'
P=[r"maximum context length is (\d+)",r"max_model_len[^\d]{0,4}(\d+)",r"\d+\s*tokens?\s*>\s*(\d+)"]
def go(n):
    b=json.dumps({"model":MODEL,"messages":[{"role":"user","content":F*(n//len(F)+1)}],
                  "max_tokens":16,"stream":False}).encode()
    r=urllib.request.Request(f"{BASE}/chat/completions",data=b,method="POST",
        headers={"content-type":"application/json","authorization":f"Bearer {KEY}"})
    try:
        with urllib.request.urlopen(r,timeout=180) as x: return x.status,x.read().decode('utf-8','replace')
    except urllib.error.HTTPError as e: return e.code,e.read().decode('utf-8','replace')
    except Exception as e: return 0,f"{type(e).__name__}: {e}"
for n in (50_000,200_000,800_000,2_000_000):
    s,t=go(n); print(f"\n--- {n:,} 字 → HTTP {s}")
    if s==200: print("    正常回答,加大再試"); continue
    print("    訊息:"," ".join(t.split())[:400])
    for p in P:
        m=re.search(p,t,re.I)
        if m: print(f"\n✅ 上限 = {m.group(1)} — 學得到,不設也行"); raise SystemExit
    if s==413: print("\n⚠️ 413 被 body 大小擋掉,不算,繼續"); continue
    print("\n❌ 拒絕但沒講數字 → 學不到,必須自己設 context_limit"); raise SystemExit
print("\n❌ 200 萬字仍正常回答 → 有東西在悄悄截斷,而且偵測不到")
PY
```

## 12. 開發指令速查

```bash
# 後端
uv sync
uv run coverage run -m pytest && uv run coverage report   # 測試 + 覆蓋率
uv run ruff check && uv run ruff format --check            # lint + 格式
uv run ty check                                            # 型別檢查

# 前端（web/）
cd web && pnpm install
pnpm run dev          # 開發伺服器（5173，proxy 後端）
pnpm run build        # 打包 web/dist（後端自動掛載）
pnpm run typecheck
```

---

## 13. 檢索品質 eval 排程（#535）

一套**離線、零 domain knowledge** 的檢索品質量測：從每個 collection 抽樣 chunk，用 LLM
反向生一個「這段能回答的問題」（Promptagator），丟進**現況** retriever，量原本那個 chunk
有沒有回到 top-k（`recall@k` / `MRR`）。語料本身就是標準答案——不需要人工標註。它是
KG（#534）/ enrichment（#533）動工前的 **baseline**：之後任何改動有沒有變好、有沒有回歸，
都靠這個數字。

運作方式是一條 specstar fan-out job（`dispatch → split → batch → finalize`，同 #227 索引
fan-out），結果寫成 `EvalResult` resource，multipod-safe（數字在 DB，不是某個 pod 的 stdout）。

### 前提

- **要設定 KB LLM**（`kb_llm`）——問題生成需要它；沒有就不會建 eval coordinator，`/api/eval-job`
  route 也不存在。
- **要有東西在消費 `eval` JobType**：
  - all-in-one（`RUN_CONSUMERS=true`）→ API 進程自己消費，不用另起 pod；
  - split 部署（`RUN_CONSUMERS=false`）→ 用 `kubernetes/base/workers.yaml` 裡的
    **`rca-worker-eval`**（`python -m workspace_app.worker eval`）。
- split 部署需**共用 Postgres backend**（producer 與 worker 看到同一個 queue）。

### 觸發一輪

k8s 定時觸發由 **`kubernetes/base/cronjob-eval.yaml`** 的 CronJob（`rca-eval-nightly`）負責——
每晚 `POST /api/eval-job` 送一個 dispatch job：

```bash
curl -fsS -X POST http://rca-app/api/eval-job \
  -H 'Content-Type: application/json' \
  -d '{"payload":{"kind":"dispatch","run_label":"'"$(date +%F)"'","sample_size":300}}'
```

送出後自動 fan-out（拉所有 collection → 抽樣 → 分批算分 → 彙總），**每個 collection 寫一份**
`EvalResult`。手動測一輪就是直接跑上面這個 `curl`（要有 worker/all-in-one 在消費）。

> 排程與樣本可調：CronJob 的 `schedule`（cron，UTC；例如 `0 2 * * 6` 只在週六跑）、`sample_size`、
> `run_label`。若部署對 create route 有鎖權限，在該 `curl` 補上對應的驗證 header。

### 看結果

specstar 自帶 CRUD route，不需自訂 endpoint：

```
GET /api/eval-result                       # 列全部
GET /api/eval-result?qb=...                # 依 collection_id / run_label 過濾
GET /api/eval-result/{id}                  # 單筆（含 recall@{1,3,5,10} + MRR，chunk 與 doc 兩級）
```

`run_label` 保留歷史，所以同一 collection 不同日期的數字可以直接比較看趨勢。

---

## 14. 知識圖譜：指標抽取排程（#534）

從投影片的 VLM 文字裡把**指標數字**（指標 / 期別 / 數字 / 單位）挖出來，存成一張扁平、
可查的 `GraphClaim` 表——之後就能「列出某指標跨所有 deck 的所有值」。這是知識圖譜（#534）
的第一步；矛盾偵測、實體消歧等是後續 slice。

### 前提與開關

- **要設定 KB LLM**（抽取要用）——沒有就不會建 graph coordinator，`/api/graph-job` route
  也不存在。
- **per-collection opt-in**：抽取是**貴的 VLM/LLM 工**,只對「有指標」的 collection 才有意義,
  所以擁有者要在 collection 設定把 **`use_graph`** 打開（default OFF，跟 `auto_digest` 同理，
  不會偷偷對全部開）。dispatch **只跑 `use_graph` 開的 collection**。
- **要有東西消費 `graph` JobType**：all-in-one（`RUN_CONSUMERS=true`）→ API 自己消費；split
  部署 → `kubernetes/base/workers.yaml` 的 **`rca-worker-graph`**。split 需共用 Postgres。

### 觸發

k8s 由 **`kubernetes/base/cronjob-graph.yaml`**（`rca-graph-weekly`）負責——**每週六**
`POST /api/graph-job` 送一個 dispatch job（排週末,對上閒置 GPU;抽取是冪等 wipe+rewrite,
每晚全量重抽會浪費）：

```bash
curl -fsS -X POST http://rca-app/api/graph-job \
  -H 'Content-Type: application/json' \
  -d '{"payload":{"kind":"dispatch"}}'
```

送出後自動 fan-out（每個 opted-in collection → 每批 doc → 抽取 → 寫 `GraphClaim`）。手動測
一輪就是跑上面這個 `curl`。

> 排程可調：CronJob 的 `schedule`（例如 `0 3 * * *` 改每晚）。若部署對 create route 有鎖權限,
> 在 `curl` 補驗證 header。

### 看結果

specstar 自帶 CRUD route,不用自訂 endpoint：

```
GET /api/graph-claim?qb=norm_metric==<指標>   # 列出某指標在所有 deck / 期別的值
GET /api/graph-claim/{id}                      # 單筆（含 provenance:來自哪個 deck/chunk）
```
