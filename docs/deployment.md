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
    ```

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
  回填前後的行為對照見 [資料遷移](migrations.md) §6。

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
| `TOOL_ARTIFACT_HOSTS` — 憑證能被送到哪些網域（逗號分隔），給 sandbox-host 與 mcp-runner 映像 | 一次（換 artifact store 才改） | 憑證**永遠不會被送出**，private 的 GitLab project 抓不到。這是刻意的預設，理由見 §15.7 |
| `TOOL_ARTIFACT_TOKEN` — 讀 artifact 用 | 一次 | private 的抓不到（public 仍可）。**只有 host 需要**，app 從不持有 |
| `grant keygen --as <代號>` → 公鑰進 `TRUSTED_KEYS` → 發版（§15.6） | **每人**一次 | `TRUSTED_KEYS` 是空的 ⇒ 任何憑證都驗不過 ⇒ **一支第三方工具都上不了架** |
| 發布 mcp-runner 映像（§15.7） | 每次發版 | 工程師沒辦法在自己的編輯器裡用這些工具。平台本身不受影響 |

`TOOL_BUILDER_ID` 三個映像必須同一個值。不同步正是那道閘門存在的理由——它會擋下來，
而不是讓它在執行期壞掉；有測試釘住三顆映像都帶這個旋鈕。

**每支工具要做的**是另一回事，而且只有兩件：發一張憑證（§15.6），以及把名字和網址寫進
`app.json` 再發版（§15.2）。

### 15.2 上架一支新工具### 15.2 上架一支新工具

```sh
# 1. 先驗（不會執行對方的程式碼，只做抓取 + 閘門 + 結構比對）
TOOL_BUILDER_ID=<這個部署的值> TOOL_ARTIFACT_TOKEN=<你的 token> \
  uv run python -m workspace_app.tooling.verify \
    'https://gitlab.example/api/v4/projects/7/jobs/artifacts/main/raw/dist/tool.manifest.json?job=build-tool' \
    --name wafer-history

# 2. 過了就登記進 app.json，然後發版
#    "agent": { "tools": [..., "wafer-history"],
#               "external_tools": { "wafer-history": "<同一個網址>" } }
```

**名字是我們定的**（`external_tools` 的 key），而且它就是憑證上的 `tool`。作者的 command
叫什麼完全不參與，所以兩個作者都叫 `data-fetch` 也不會互相蓋掉——各發一張憑證、各取一個名字。

先發憑證（§15.6）再登記：沒有憑證的 artifact，`verify` 和 host 都會拒絕。

### 15.3 換版本：不用做任何事

網址指的是「最新的 artifact」，作者 push 完，**下一個開起來的 sandbox 就是新版**。
不用改 repo、不用重新部署。

### 15.4 退回某一版

把 `external_tools` 的網址從「最新」改成**指定那次 build** 的 artifact
（GitLab 的 `/jobs/<job_id>/artifacts/…`），然後發版。sha 若還在該 host 的快取裡是秒回，
被回收了就重抓一次，同一條路。

> 「跟著最新」和「釘死某版」是**同一個欄位的兩種寫法**，沒有第二套機制。

### 15.5 磁碟

每支 bundle 約 150MB，而且**新舊版本會並存**（舊的留著，回滾才會是重掛而不是重抓）。
用 `SANDBOX_HOST_TOOL_CACHE_MAX_BYTES` 設上限：超過就由舊到新淘汰，
但**正在被使用的永遠不會被淘汰**。粗估：`工具數 × 保留版本數 × 150MB × host 數`。
**沒設上限 = 不淘汰任何東西**（和這個 repo 其他限制的慣例一致:未設即無限)。要讓 reaper
真的回收磁碟,就給一個數字——那時才會由舊到新淘汰沒被引用的版本。

### 15.6 工具憑證(准入 + 體積)

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

### 15.7 讓工程師用自己的 agent 跑同一支工具（MCP runner）

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

### 15.8 出事時怎麼查

- **某支工具突然不見**：agent 的 prompt 裡會有一段「Tools that are unavailable right now」
  寫著原因。最常見的是 GitLab artifact 過期（作者的 CI 沒設 `expire_in: never`）。
- **工具在跑但行為怪怪的**：resolve 的紀錄有 `name → sha + version`，可以確認那個 turn
  用的是哪一版。
- **artifact store 連不上**：host 會用**上次成功**的版本繼續服務並標記 `stale`，
  不會讓 workspace 開不起來。

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
