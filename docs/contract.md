# RCA 3.0 — 線上契約（Wire Contract）

FE ↔ BE 邊界的**單一事實來源**。任何更動線上格式
（資料模型、route、SSE 事件）的人，都要在同一個 commit 裡
更新本文件；`plan-backend.md` 與 `plan-frontend.md` 也都
回頭引用這裡。

架構立場:**後端是 RCA-agnostic 的。** 它儲存
`Investigation` 的 metadata 加上對話歷史、跑 agent 與
sandbox(沙箱)、並提供檔案服務。所有 RCA 特有的結構(5-Why、
fishbone、report 章節、hypothesis cell、corrective action…)
都是 **agent 寫進 FileStore 的純 `.md` /
`.ipynb` / `.csv` / `.json` / `.canvas` 檔案資料**。FE 的 renderer 用副檔名
判斷檔案型別,再套上對應的 renderer。

---

## 1. specstar 模型

三個 resource，透過 `register_all(spec)` 註冊。specstar
自動產生 REST route(`/investigation`、`/agent-config`、
`/conversation`),並自動補上 metadata(`resource_id`、
`created_time`、`updated_time`、`created_by`、`updated_by`)。

### 1.1 `Investigation`

```python
from enum import StrEnum
from typing import Annotated

from msgspec import Struct, field
from specstar import OnDelete, Ref


class Severity(StrEnum):
    P0 = "P0"   # halt（停線）
    P1 = "P1"   # critical（嚴重）
    P2 = "P2"   # major（重大）
    P3 = "P3"   # minor（次要）
    P4 = "P4"   # cosmetic（外觀）


class Status(StrEnum):
    """Investigation status flow.
       create → TRIAGING → AWAITING_REVIEW → RESOLVED  (happy path)
                                          └→ ABANDONED  (closed without RC)
    """
    TRIAGING = "triaging"
    AWAITING_REVIEW = "awaiting_review"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class Investigation(Struct):
    title: str                                            # required（必填）
    owner: str                                            # required — user id；透過公司 API 解析
    description: str = ""                                 # 多行；即設計裡的「initial brief」
    severity: Severity = Severity.P2
    status: Status = Status.TRIAGING
    product: str = ""                                     # part / board（例如 "MX-7 board"）
    members: list[str] = field(default_factory=list)      # 額外的 user id
    topics: list[str] = field(default_factory=list)       # 自由格式標籤（"Reflow zone-3"…）
    attached_agent_config_id: Annotated[
        str | None, Ref("agent_config", on_delete=OnDelete.set_null)
    ] = None
    template_profile: str = "default"                     # 此 investigation 由哪個 template seed 出來
```

`template_profile` 記錄這個 investigation 是從哪個 template 建立的;
它會被持久化,好讓 agent 的 system prompt 在 turn 時能跟
該 template 的 starting-files 附錄組合起來(base prompt +
`rca/templates/{profile}/_prompt.md`)。

`owner` **沒有預設值** — 每個 investigation 在建立時都必須宣告
建立者。API 層讀取目前的使用者(v1:
永遠是 `"default-user"`)並填入;v2 的 SSO 會把它換成
真正的 user id。

`attached_agent_config_id` 是指向 `AgentConfig` 的 `Ref`。若所
參照的 config 被刪除,這個欄位會自動清成 `None`(該
investigation 仍可用 API factory 建出的任一預設 agent 繼續運作)。

### 1.2 `Conversation`

```python
class Message(Struct):
    role: str                                    # user / assistant / tool / system
    content: str
    author: str | None = None                    # role=user 時為 user id；
                                                 # role=assistant 時為 agent name
    reasoning: str | None = None                 # LLM 的 reasoning / thinking 內容
                                                 # (Qwen3 <thinking>、OpenAI o-series…)
    tool_call_id: str | None = None              # role=tool
    tool_name: str | None = None                 # role=tool
    tool_args: dict[str, Any] | None = None      # role=tool — 呼叫參數（從 ToolStart 擷取）
    created_at: int | None = None                # epoch ms；重新載入時還原 log 時間戳


class Conversation(Struct):
    investigation_id: Annotated[
        str, Ref("investigation", on_delete=OnDelete.cascade)
    ]
    messages: list[Message] = field(default_factory=list)
```

`investigation_id` 是帶 `cascade` 的 `Ref` — 刪掉
investigation 會連同它的 conversation 一起刪。

`Message.author` 在 `role == "user"` 時帶 user id(讓
多人 UI 能標出「Alice / 14:30:12」對「Bob / 14:31:05」),在
`role == "assistant"` 時帶 agent 識別字(為多 agent 設定預留
前向相容;v1 就是當前 `AgentConfig.name`)。

`Message.reasoning` 把模型的 chain-of-thought 從
`content` 分離出來。Qwen3 把 `thinking` 當成同層欄位回傳;OpenAI 的
o-series 回傳 reasoning item;我們的 runner 把兩者合併進
這一個欄位。FE 可以把它摺疊呈現(ChatGPT 風格的「Show
thinking」),而不跟 assistant 給使用者看的答案混在一起。

### 1.3 `AgentConfig`

```python
class AgentConfig(Struct):
    name: str
    model: str = "ollama_chat/qwen3:14b"
    system_prompt: str = ""                                 # RCA prompt 載入於此
    allowed_tools: list[str] = field(default_factory=list)  # 子集；空 = 全部
    env: dict[str, str] = field(default_factory=dict)
    sandbox_image: str = "workspace-app/sandbox:py312-ds"
    idle_timeout_seconds: int = 28800                       # 8 hours（8 小時）
```

### 1.4 設計裡顯示、但模型上「不」儲存的欄位

設計上顯示了許多 BE 不持久化的 surface 欄位;FE
從上面的事實來源加上旁邊的狀態自行推導。

| 設計 surface | 來源 |
|---|---|
| `INC-2026-0142` | specstar `resource_id`;FE 格式化顯示字串 |
| `summary`(表格列的第 2 行) | `description` 的第一句/第一行;FE 推導 |
| `sevTone` / `statusTone`(顏色) | FE 的顏色對照常數 |
| `updated`(「12 min ago」) | specstar `updated_time` + FE 相對時間格式化 |
| `agent: "running" \| "idle"` | 當且僅當 BE registry 裡 `session.current_turn` 還活著時為真 |
| `pinned` | client 端 `localStorage` 偏好(BE 不儲存) |
| `lot` | 已捨棄 — 僅以純文字出現在 agent 敘述／notebook 程式碼裡 |
| `reportV` / `reportProgress` | 由 `/report.v*.md` 的檔案清單 + agent 執行狀態推導 |

---

### 1.5 KB 模型(`Collection` / `SourceDoc` / `DocChunk` / `KbChat`)

```python
class Collection(Struct):                 # → resource "collection"
    name: str
    description: str = ""
    # #328：per-collection 可調 parser 設定，keyed by parser_id
    # (type(parser).__name__，與 DocChunk.parser_id 同 key) → {knob: value}。
    # 非索引（不過濾／排序）⇒ 免 migration。
    parser_configs: dict[str, dict[str, Any]] = {}

class SourceDoc(Struct):                  # → resource "source-doc"
    # id = encode_doc_id(collection_id, path)：natural key，把每個 '/'
    # 換成 '∕' (U+2215) —— slash-free（specstar id 不能含 ASCII '/'），
    # 以 path 為 key（NOT per-user），NOT percent-encoded。OPAQUE —— 絕不解析；
    # path/collection_id 從 record + created_by meta 讀。
    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    path: str                             # 上傳內的相對路徑
    content: Binary                       # 原始 bytes；content.file_id = xxh3（dedup）；
                                          # content.content_type 透過 magic 自動 sniff
    text: str | None = None               # 衍生／擷取的文字（None ⇒ 解碼 content）
    status: str = "ready"                 # indexing | ready | error（非同步 index 期間設定）
    # #328：per-doc 覆寫一份 prompt/param 驅動 parser 的設定（同樣
    # parser_id → {knob: value}），在 precedence merge 裡「勝過」collection
    # 的 parser_configs。非索引 ⇒ 免 migration；
    # index 時由 kb.parser_config.effective_config 解析（parser 預設 <
    # collection.parser_configs < 此 per-doc override）。
    parser_config_overrides: dict[str, dict[str, Any]] = {}

class DocChunk(Struct):                   # → resource "doc-chunk"（衍生；只保留當前版）
    collection_id: str
    source_doc_id: Annotated[str, Ref("source-doc", on_delete=OnDelete.cascade)]
    seq: int
    start: int                            # canonical（normalized）文字裡的字元 offset
    end: int
    text: str
    embedding: Annotated[list[float], Vector(dim=EMBED_DIM, distance="cosine")]

class Citation(Struct):                   # KB 答案裡一個已解析的 [n] marker
    marker: int                           # 即 [n]
    collection_id: str
    document_id: str                      # opaque 的 SourceDoc id（見 encode_doc_id）
    filename: str                         # basename(path)
    start: int                            # 併入 canonical 文字後的 span
    end: int
    source_chunk_ids: list[str]           # 組成被引用段落的 DocChunk id
    snippet: str = ""

class KbMessage(Struct):
    role: str                             # user / assistant / tool
    content: str = ""
    reasoning: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    citations: list[Citation] = field(default_factory=list)
    created_at: int | None = None         # epoch ms

class KbChat(Struct):                     # → resource "kb-chat"
    title: str = "New chat"
    collection_ids: list[str] = field(default_factory=list)
    messages: list[KbMessage] = field(default_factory=list)
```

`EMBED_DIM = int(os.getenv("KB_EMBED_DIM", "1024"))` — 儲存的向量寬度;
必須與 embedder 的輸出相符。`DocChunk` 是衍生的,且在
re-index 時**硬刪除**(soft delete 會在向量／關鍵字搜尋裡留下過時的 chunk)。

#### `CodeWikiBuildRun`(#281 code-wiki fan-out 的 join state)

```python
class CodeWikiBuildRun(Struct):           # → resource "code-wiki-build-run"
    # resource id == collection id（一個 collection 一個 run）
    collection_id: Annotated[str, Ref("collection", on_delete=OnDelete.cascade)]
    total: int                            # code_split 規劃出的 batch 數
    done: list[int] = field(default_factory=list)    # 成功的 batch index
    failed: list[int] = field(default_factory=list)  # 放棄的 batch index
    finalized: bool = False               # 只跑一次的 finalize gate（CAS 認領）
    status: str = "running"               # running | done | error
    phase: str = "cards"                  # cards | finalizing
```

`add_model(CodeWikiBuildRun, indexed_fields=["status"])`。一個 code(`git_url`)
collection 的 wiki 由「讀原始碼」分層生成(L0 per-file → L1 目錄 roll-up
→ L2 architecture/topics/index),重量級的 L0 工作 fan-out 成許多小 job,
靠這個 row 以 specstar etag CAS 做 join(mirrors `IndexRun`)。

它**沿用既有的 wiki JobType**(`WikiMaintenanceJob` / `WikiJobPayload`,
不新增 JobType):`WikiJobPayload.op` 詞彙從 `fold | unfold` 擴充為
`fold | unfold | code_split | code_card | code_finalize`,並新增兩個只由
`code_card` 攜帶的欄位 `batch_index: int` 與 `batch_paths: list[str]`。
**不新增 HTTP route** — code build 由既有的 collection sync
(`POST /kb/collections/{id}/sync`)與 wiki rebuild
(`POST /kb/collections/{id}/wiki/rebuild`,code 分支)觸發。Config 不新增 key:
code-wiki summariser 重用 `kb.wiki.llm` preset(空值 ⇒ code-wiki 關閉)。

---

## 2. HTTP route

除另有說明外,所有路徑皆為 JSON。Auth:v1 裡每個請求都隱含
以 `default-user` 身分執行(無 header、無 token)。等真正的 auth
落地時,本節會補上 `Authorization:` 的要求。

### 2.1 Investigation 生命週期

| Method | Path | 用途 | 狀態 |
|---|---|---|---|
| `GET`    | `/investigation`                       | 列出 investigation（specstar auto） | ✅ |
| `POST`   | `/investigation`                       | **自訂包裝:** 建立 + seed 預設 template 檔案 + 從 TRIAGING 起始 | ✅ |
| `GET`    | `/investigation/{id}`                  | 取單一（specstar auto） | ✅ |
| `PATCH`  | `/investigation/{id}`                  | 部分更新（specstar auto） | ✅ |
| `DELETE` | `/investigation/{id}`                  | 軟刪除（specstar auto） | ✅ |
| `POST`   | `/investigations/{id}/close`           | 手動 close：`{"status": "resolved" \| "abandoned"}` → 拆掉 sandbox | ✅ |

### 2.2 Chat / agent turn

| Method | Path | 用途 | 狀態 |
|---|---|---|---|
| `POST`   | `/investigations/{id}/messages`            | 送出 user message → `AgentEvent` 的 SSE 串流 | ✅ |
| `DELETE` | `/investigations/{id}/messages/current`    | 中斷進行中的 turn（RunCancelled 送到舊串流） | ✅ |

POST body 形狀:
```json
{ "content": "string" }
```

### 2.3 Files

| Method | Path | 用途 | 狀態 |
|---|---|---|---|
| `GET`    | `/investigations/{id}/files[?prefix=<p>]` | 列出檔案：`[{"path", "size"}]`           | ✅ |
| `GET`    | `/investigations/{id}/dirs`               | 目錄路徑（含空目錄，供樹狀用）：`[string]` | ✅ |
| `GET`    | `/investigations/{id}/files/{path:path}`  | 讀檔案內容（text/plain 或 octet-stream） | ✅ |
| `PUT`    | `/investigations/{id}/files/{path:path}`  | 寫入原始 bytes（FE 在此自動存 notebook）→ 204 | ✅ |
| `DELETE` | `/investigations/{id}/files/{path:path}`  | 刪一個檔案**或**目錄子樹 → 204（不存在則 404） | ✅ |
| `POST`   | `/investigations/{id}/files/mkdir`        | 建空目錄：body `{"path"}` → 204（若有檔案佔位則 409） | ✅ |
| `POST`   | `/investigations/{id}/files/move`         | 重新命名／搬移檔案或目錄子樹：body `{"from", "to"}` → 204（搬進自己 400、缺失 404、目標已存在 409） | ✅ |
| `POST`   | `/investigations/{id}/files/copy`         | 複製檔案或目錄子樹：body `{"from", "to"}` → 204（錯誤同 move） | ✅ |

### 2.3b 搜尋／取代（VSCode 搜尋面板）

| Method | Path | 用途 | 狀態 |
|---|---|---|---|
| `POST`   | `/investigations/{id}/search`  | 全文搜尋 → `[{"path", "matches": [{"line","col","text"}]}]` | ✅ |
| `POST`   | `/investigations/{id}/replace` | 跨檔案搜尋 + 取代 → `{"replaced": int}` | ✅ |

搜尋／取代 body（`replace` 多帶一個 `replacement`):
```json
{ "query": "string", "regex": false, "caseSensitive": false,
  "wholeWord": false, "include": "", "exclude": "", "replacement": "" }
```
空的 `query` → no-op(`[]` / `{"replaced": 0}`);無效的 regex 回 422。
Binary(非 UTF-8)檔案會被跳過。

### 2.3c 直連 sandbox shell（Terminal 面板）

| Method | Path | 用途 | 狀態 |
|---|---|---|---|
| `POST`   | `/investigations/{id}/exec` | **同步**執行一個 shell 指令：body `{"cmd": [string]}` → `{exit_code, stdout, stderr}`；空 `cmd` 回 422 | ✅ |

> 注意:這是 **Terminal** 面板的 one-shot exec(回傳時給出完整結果)。
> 它跟 agent 的 `exec` *tool* 不同 — 後者在一個 turn 期間以
> `ToolLog` 事件即時串流 stdout(見 §3.1)。

### 2.4 Notebook 執行

| Method | Path | 用途 | 狀態 |
|---|---|---|---|
| `POST`   | `/investigations/{id}/notebooks/{path}/cells/{idx}/execute` | 執行 cell：body `{"code": "string"}` → `CellEvent` 的 SSE 串流 | ✅ |
| `DELETE` | `/investigations/{id}/notebooks/{path}/cells/{idx}/execute` | 中斷 cell                                | ✅ |
| `POST`   | `/investigations/{id}/notebooks/{path}/kernel/restart`      | 重啟 per-notebook kernel → 204             | ✅ |

### 2.5 Meta

| Method | Path | 用途 | 狀態 |
|---|---|---|---|
| `GET`    | `/templates` | New Investigation picker 的 template profile 名稱 | ✅ |
| `GET`    | `/activity`  | 近期活動 feed（最新在前）：`[{ts, kind, text, ref}]` | ✅ |
| `GET`    | `/help`      | Platform Help 頁資訊（掛在 `/api` 之下 → `GET /api/help`）：`HelpInfo`；idempotent，按需 resolve Help collection，永不 404 | ✅ |
| `GET`    | `/tools`     | 扁平 tool catalog（聊天 tool card 標籤用）：`[ToolCatalogEntry{name, label, description}]` | ✅ |

`POST /investigation` body:
```json
{ "title": "string", "owner": "string", "description": "",
  "severity": "P2", "status": "triaging", "product": "",
  "members": [], "topics": [],
  "attached_agent_config_id": null, "template_profile": "default" }
```
`title` + `owner` 必填;其餘照上面預設。未知的
`template_profile` 回 422。Activity `kind` ∈
`investigation_created | investigation_closed | session_closed |
file_written | file_moved | file_copied | file_deleted |
dir_created | dir_deleted | agent_turn_complete`。

`GET /help`(#230)回傳型別:
```json
{ "collection_id": "string",
  "documents": [{ "id": "string", "path": "string",
                  "title": "string", "kind": "release_notes" | "guide" }] }
```
`HelpInfo` 讓 FE 把 KB chat scope 到 Platform Help collection,並把每份
文件連進 KB 文件檢視器(`id` 是 opaque 的 `SourceDoc` id)。`kind`:
`CHANGELOG.md` → `release_notes`,其餘 → `guide`。

### 2.6 Specstar admin（自動產生，藏在 `/docs` 後）

specstar 為每個註冊的 resource 發出約 30 條 route(CRUD + meta + blob
+ revision + search)。FE 只用上面列的那幾條。那些
自動產生的 route 仍可呼叫,供 admin/debug 用;可在
`GET /openapi.json` 及 `GET /docs` 的互動式 Swagger UI 看到。

### 2.7 Report — 沒有專屬端點

Report 採用 `/report.v{N}.md` 的檔名慣例:
- Agent 透過 `write_file` 寫 `/report.v1.md`、`/report.v2.md`…。
- FE 透過 `GET /investigations/{id}/files?prefix=/report.v` 列出 `/report.v*.md`。
- N 最大者為**當前**;其餘為**被取代**。
- 「Generate new version」就只是一個 agent chat prompt — agent 寫出
  下一個 `/report.v{N+1}.md`。沒有特殊端點。

### 2.8 RCA 領域的 agent tool — 沒有

沒有 `spc_read`、`defects_aoi`、`pareto_build` 等 route。Agent
只用泛用 tool(`exec`、`read_file`、`write_file`、`ls`、
`exists`、`delete_file`),由 system prompt 教它 RCA
工作流／檔案慣例。模擬的 SPC / AOI 資料以 CSV
fixture 放在 seed 出來的 template 內(`/data/*.csv`)。

### 2.9 KB chatbot

| Method | Path | 用途 | 狀態 |
|---|---|---|---|
| `GET`    | `/kb/agent`                              | KB agent 顯示名稱 + quick-prompt 建議：`{name, suggestions}` | ✅ |
| `POST`   | `/kb/collections`                        | 建立 collection：body `{name, description?}` → `{resource_id, name, description}` | ✅ |
| `GET`    | `/kb/collections`                        | 列出 collection：`[{resource_id, name, description}]` | ✅ |
| `POST`   | `/kb/collections/{id}/documents`         | multipart 上傳（`file`）；快速存檔 + 背景 index → `{document_ids, status:"indexing"}`。upload check 拒絕（加密／無法讀取的 Office／PDF）時回 **422** `detail: {check_id, reason_code, message_key}`，**不**存任何東西 | ✅ |
| `GET`    | `/kb/collections/{id}/documents`         | 列出 doc：`[{resource_id, path, content_type, created_by, status}]` | ✅ |
| `GET`    | `/kb/upload-checks`                       | 瀏覽器可跑的 upload-check 提示描述：`[{id, extensions, forbid_magic_hex, message_key}]`（純 server 端的 check 省略，如 PDF） | ✅ |
| `GET`    | `/kb/documents?id={doc_id}`              | render 一份文件 → `{filename, collection_id, markdown}`（相對連結改寫為 `kb://doc/{id}`）。`id` 是 opaque 的 SourceDoc id，用 query param 讓這個 slash-free token 能在 URL 裡 round-trip | ✅ |
| `POST`   | `/kb/chats`                              | 建立 thread：body `{title?, collection_ids}` → `{resource_id, title, collection_ids}` | ✅ |
| `GET`    | `/kb/chats`                              | 列出 thread：`[{resource_id, title, collection_ids, message_count}]` | ✅ |
| `GET`    | `/kb/chats/{id}`                         | thread 細節：`{resource_id, title, collection_ids, messages:[KbMessage…]}`（缺失則 404） | ✅ |
| `DELETE` | `/kb/chats/{id}`                         | 刪一個 thread → 204（硬刪除） | ✅ |
| `POST`   | `/kb/chats/{id}/messages`                | 送出 user message → `AgentEvent` 的 SSE 串流（與 RCA 同一 union）；持久化答案 + `[n]` citation | ✅ |
| `DELETE` | `/kb/chats/{id}/messages/current`        | 中斷進行中的 turn（RunCancelled 送到舊串流）；即使閒置也回 204 —— 與 RCA 端點一致 | ✅ |

資料夾上傳 = FE 把每個檔案以其相對路徑當 multipart
filename 來 POST(一個檔案一個 SourceDoc,跟解開壓縮檔一樣)。Citation **不**
在 SSE 串流裡 — 在 `done` 時 refetch `GET /kb/chats/{id}` 取回
持久化的 assistant `KbMessage` 與其已解析的 `[n]` `Citation`。

一個 SourceDoc 的 `resource_id` 是它的 natural key `{collection_id}/{path}`
(**以 path 為 key,非 per-user**),把每個 `/` 換成 `∕`(U+2215)成為一個
slash-free token(specstar id 不能含 ASCII `/`) — **不是** percent-encode,
因為這個 id 會出現在使用者看得到的 `kb://doc/{id}` 連結裡。它是 **opaque** 的 —
FE/後端絕不解析它;`path`/`collection_id` 來自 record +
`created_by` meta。KB chat 重用跟 RCA workspace **同一個 turn engine**
(每個 conversation 一個可取消的進行中 turn),所以它的
串流 + 中斷契約完全相同。

### 2.10 App 平台 item route(post-RCA-3.0)

> 本文件仍是 RCA-3.0 時代,**尚未**完整追蹤 App 平台的
> `/a/{slug}/items/{item_id}/…` route 家族(那是 `Investigation` →
> `WorkItem` 遷移後的後繼路徑)。以下只列近期新增、且本文件需反映的幾條。

| Method | Path | 用途 | 狀態 |
|---|---|---|---|
| `GET`    | `/a/{slug}/items/{item_id}/tools`     | per-item tool picker 狀態:`ItemTools{tools:[ItemToolState{key, label, description, default_on, pref:"follow"\|"on"\|"off", effective}]}`;`effective` 由 turn 用的同一條 resolve 在 server 端算出（anti-drift） | ✅ |
| `GET`    | `/a/{slug}/items/{item_id}/workflows` | 列出此 item `.workflows/` 內使用者自寫的 workflow manifest(id + title + phases) | ✅ |

`pref` 對應 `WorkItemBase.attached_tool_prefs: dict[str, bool]`(#322,Tier-1
稀疏 tri-state override:有 key 釘 on(`True`)／off(`False`),沒 key 跟著
profile/App 預設;override 上限是 `app.json` 的 `tools`,**不是** profile)。
`.workflows/` 的 workflow 由 agent tool **`save_workflow`**(#323)寫入
`<workspace>/.workflows/<id>.json`。

---

## 3. SSE 事件型別

兩個**各自獨立**的事件 union,透過兩個不同的
端點串流。兩者都把一個 JSON 物件序列化成一行 `data:`。

### 3.1 `AgentEvent` — 走 `POST /investigations/{id}/messages`

對應 `web/src/events.ts`。**KB chat**(`POST /kb/chats/{id}/messages`,
§2.9)串流**同一個 union** — KB agent 重用 runner,FE
用共用的 agent-log view 渲染兩個 chat。

| Variant | 形狀 | 終止? | 備註 |
|---|---|---|---|
| `MessageDelta`        | `{type: "message_delta", text: string, reasoning?: boolean}` | 否 | append 到 assistant message;若 `reasoning=true`,改 append 到 reasoning channel |
| `ToolStart`           | `{type: "tool_start", call_id: string, name: string, args: object}` | 否 | |
| `ToolEnd`             | `{type: "tool_end", call_id: string, output: string}` | 否 | |
| `ToolLog`             | `{type: "tool_log", text: string, call_id: string?}` | 否 | 執行中 tool 的即時 stdout chunk;空 `call_id` 附到最近一個執行中的 call |
| `RunDone`             | `{type: "done"}` | **是** | 正常完成 |
| `RunError`            | `{type: "error", message: string}` | 是 | catch-all 失敗 |
| `RunCancelled`        | `{type: "run_cancelled"}` | 是 | 使用者中斷（DELETE 或新的 POST） |
| `ToolCallParseError`  | `{type: "tool_call_parse_error", hint: string, call_id: string?, raw: string?}` | 否 | 接著會做 retry-with-feedback |
| `MaxTurnsExceeded`    | `{type: "max_turns_exceeded", turns: number}` | 是 | agent 沒收斂;`turns` 是 runner 設定的預算 |
| `AgentMetrics`        | `{type: "agent_metrics", phase: "up"\|"down"\|"final", prompt_tokens, completion_tokens, elapsed_ms}` | 否 | 即時 token telemetry（↑/↓ tok/s）；up/down 為近似值，final 是回報時的精確 usage |

延後(已在 FE 宣告供未來使用,但尚未發出):
- `SandboxKilledIdle` `{type: "sandbox_killed_idle"}` — 需要 registry refactor。

### 3.2 `CellEvent` — 走 `POST /investigations/{id}/notebooks/{path}/cells/{idx}/execute`

隨 plan-backend §7.3 一起落地。

| Variant | 形狀 | 終止? | 備註 |
|---|---|---|---|
| `CellStream`       | `{type: "cell_stream", stream: "stdout" \| "stderr", text: string}` | 否 | append 到 cell 輸出 |
| `CellDisplayData`  | `{type: "cell_display_data", data: {<mime>: string, ...}}` | 否 | mime bundle：`image/png` base64、`text/html`、`text/plain` |
| `CellError`        | `{type: "cell_error", ename: string, evalue: string, traceback: string[]}` | 否 | 渲染為紅色 |
| `CellDone`         | `{type: "cell_done", execution_count: number}` | **是** | 結算 cell + 關閉串流 |

### 3.3 SSE framing

標準的 `text/event-stream`。每個事件是單獨一行 `data:`,
後面跟一行空行:

```
data: {"type":"message_delta","text":"hello"}

data: {"type":"done"}

```

v1 沒有 `event:` 或 `id:` 行。(Reconnect / `Last-Event-ID` 延後處理。)

---

## 4. 品牌與靜態資源

| Path | 來源 | 用途 |
|---|---|---|
| `/rca-mark.svg`           | `design_handoff_rca_3.0/assets/rca-mark.svg`           | 主標誌，淺色背景 |
| `/rca-mark-light.svg`     | `design_handoff_rca_3.0/assets/rca-mark-light.svg`     | 深色背景上的標誌 |
| `/rca-logo-horizontal.svg`| `design_handoff_rca_3.0/assets/rca-logo-horizontal.svg`| 完整 lockup |
| `/favicon.ico`            | `design_handoff_rca_3.0/assets/favicon.ico`            | 分頁圖示 |

FE 在 build 時把這些複製到 `web/public/`;後端透過既有的
SPA 靜態掛載,提供 `web/dist/` 裡的內容。
**標誌頂端的橘點必須保留 — 那是品牌。**

---

## 5. Agent 遵守的檔案慣例（FE renderer 依賴它）

這些是 agent 端的慣例,由 RCA system prompt 支撐
(plan-backend §8)。BE 不強制;FE renderer
依副檔名 + 內容形狀比對:

| 檔案路徑／樣式 | 內容形狀 | Renderer |
|---|---|---|
| `/brief.md`         | Markdown —「Investigation Brief」章節 | F10 markdown |
| `/drift.ipynb`      | nbformat v4 JSON                          | F8 notebook |
| `/pareto.ipynb`     | nbformat v4 JSON                          | F8 notebook |
| `/fishbone.canvas`  | JSON：`{effect: string, branches: [{label, side, items: [{t, strong?}]}]}` | F12 fishbone SVG |
| `/5-why.md`         | 帶 `## Why #N` 標題的 Markdown        | F10 markdown（v1.5 可能加結構化 JSON 變體） |
| `/report.v{N}.md`   | Markdown：Problem statement / Findings (a/b/c/d) / Next steps | F11 report（取最大 N 為當前） |
| `/data/*.csv`       | 範例 fixture 資料（由 template seed）  | （不直接檢視；由 notebook 程式碼消費） |

---

## 6. 狀態圖例

- ✅ 已出貨（已 commit 並測試）
- ⏳ 已規劃，尚未出貨 — 章節參照見 `plan-backend.md`
- ⏸ 已延後 — 不在 v1，明確原因見 `plan-backend.md` §2

狀態變動時,要在同一個 commit 裡同時更新本文件與
`plan-backend.md` 的跨切面章節。
