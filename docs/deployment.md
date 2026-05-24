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
    async def walk(self, handle, root: str) -> list[FileEntry]: ...
    async def expose_port(self, handle, container_port: int) -> tuple[str, int]: ...
```

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
    suggestions=["分析這份 log", "畫魚骨圖", "寫 8D 報告"],
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

---

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
