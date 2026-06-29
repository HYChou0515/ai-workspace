# 詞彙表（Glossary）

本詞彙表彙整本平台跨子系統共用的領域名詞，依領域分區，每個詞附上精簡定義與「歸哪個子系統管」的連結，方便從一個名詞跳到負責它的子系統文件。

!!! info "用語權威"
    名詞的權威定義以專案根目錄的 `CONTEXT.md` 為準。請**完全照** `CONTEXT.md` 的用字，不要在程式碼、commit 或文件中漂移到別的詞（如「service」「handler」「manager」）。本表只是把那些名詞按領域整理並連到負責的子系統；遇到衝突時一律以 `CONTEXT.md` 為準。

---

## App 與 work item

平台是**多 App** 的：RCA 只是其中一個由 in-code 模板目錄產生的 App。

- **App** — 自成一格、各自品牌化的儀表板，由 in-code 目錄 `apps/<slug>/` 定義（`app.json` 身分／功能／agent／layout ＋ `model.py` 的 WorkItem Struct ＋ `prompts/` ＋ `profiles/`）。取代舊的 `agents.workspace_chat` picker。歸 [App 平台](subsystems/apps-platform.md)。
- **profile** — App *內部*的具名起始內容包（`apps/<slug>/profiles/<name>/`）：seed 檔 ＋ `_prompt.md` 提示詞附錄 ＋ `.skill/` ＋ `_profile.json`（title／description／suggestions／tools／presets／default_preset）。建立 work item 時選定。歸 [App 平台](subsystems/apps-platform.md)。
- **WorkItem / WorkItemBase** — 每個 App 的逐筆紀錄。各 App 在 `apps/<slug>/model.py` 手寫 `msgspec.Struct` 繼承 `WorkItemBase`，以 `add_model` ＋ App 的 `INDEXED_FIELDS` 註冊（per-resource 原生索引，跨 App 資料不混）。分 Tier 1（`title`/`owner`）、Tier 2（`members`/`topics` opt-in）、Tier 3（App 自有域欄位）。歸 [App 平台](subsystems/apps-platform.md)。
- **item_id** — 共用機制（FileStore、Conversation、sandbox）掛載用的橫切鍵 ＝ WorkItem 的 `resource_id`（全域唯一，**絕不**用 `uid`）。取代 `investigation_id`。歸 [App 平台](subsystems/apps-platform.md)。
- **AppCatalog** — 啟動時建立的已註冊 App 目錄（manifest ＋ profiles）＋ 逐回合解析 `app ◇ profile ◇ preset`。歸 [App 平台](subsystems/apps-platform.md)。
- **function toggles** — `app.json` 的功能開關：`workspace`（檔案 IDE ＋ 檔案工具 ＋ profile 檔案 seeding）、`sandbox`（agent `exec` ＋ 套件工具）、`terminal`（人類 shell pane，需 `sandbox`）。`tools[]` 與 toggle 不一致是**啟動硬錯誤**。歸 [App 平台](subsystems/apps-platform.md)。
- **layout** — `app.json` 的逐 surface 欄位擺放（`breadcrumb`/`statusbar`/`list`/`form` 各列出顯示欄位；`default_tabs` 列出進入時開啟的檔案）。display-only overlay。歸 [App 平台](subsystems/apps-platform.md)。
- **field schema** — FE 渲染／行內編輯 WorkItem 域欄位所需的逐欄 `{name, label, kind: select|text, options?}`，由後端從 model 的 OpenAPI schema 投影，折進 `GET /apps/{slug}` manifest 的 `fields`。model 是 enum options 的唯一來源。歸 [App 平台](subsystems/apps-platform.md)。
- **field_styles** — `app.json` 的可選 overlay，把 enum 欄位的 options 對映到語意色調 token（`danger`/`warn`/`ok`/`muted`/`accent`）。theme-aware、不用 raw hex。歸 [App 平台](subsystems/apps-platform.md)。
- **lifecycle** — `app.json` 可選 `{status_field, closing_states}`，宣告 App 的關閉／結案流程；有才顯示 Close affordance，並通用地拆除 sandbox。歸 [App 平台](subsystems/apps-platform.md)。
- **WorkspaceShell / DomainFields** — workspace 畫面是**一個**通用 shell（取代 RCA 專屬的 `InvestigationShell`），吃通用 `WorkItem` ＋ `AppManifest` ＋ field schema，透過 `DomainFields` renderer（`kind→renderer` registry：`text`/`select`）渲染域欄位；per-App 變化純為資料。歸 [前端](subsystems/frontend.md)。
- **tool catalog（ToolMeta）** — 後端工具顯示中繼資料的單一真相源：每個可呼叫工具一筆 `ToolMeta {name, label, description}`（built-in 取自 impl docstring 經 `builtin_tool_descriptions()`、套件命令取自 prebuilt bundle）。`humanize_tool_label` 提供絕不外漏 raw snake_case 的 fallback label；`flat_catalog` 餵聊天 tool cards、`picker_units` 餵逐 item 的工具挑選器。`tooling/catalog.py`。歸 [App 平台](subsystems/apps-platform.md)。
- **attached_tool_prefs（逐 item 三態工具覆寫）** — `WorkItemBase` 的 Tier-1 欄位 `dict[str, bool]`，與 `attached_preset` 並列。稀疏逐工具覆寫：有 key 把工具釘成 ON(True)／OFF(False)、無 key 跟隨 profile／App 預設。覆寫上限是 `app.json` 的 `agent.tools`（**非** profile），故 force-ON 可把 profile 收掉的工具加回；超出上限的 key 無效。由 `AppCatalog.resolve(tool_prefs=...)` 經 `_apply_tool_prefs` 套用，於 web 工具挑選器編輯。歸 [App 平台](subsystems/apps-platform.md)。
- **tool ceiling vs default set** — `app.json` 的 `agent.tools` 是 ceiling（可選工具全集，也是任何覆寫的硬上限）；profile `_profile.json` 的 `tools` 只是預設勾選子集（省略 ＝ 整個 ceiling）。挑選器每個 ceiling 項一列，顯示 `default_on`、已存的 pref（follow／on／off）與解析後的 effective 狀態。歸 [App 平台](subsystems/apps-platform.md)。

---

## Agent

- **Preset** — `agents.presets` 裡的具名 LLM 配方：`{model, prompt_file?, suggestions, allowed_tools?, env, sandbox_image, idle_timeout_seconds, llm: {base_url, api_key}}`。可被多個 caller 重用。Bundled：`qwen3-local`、`claude-opus`、`openai-mini`、`kb-default`、`infer-modules-default`、`kb-retrieval`。歸 [啟動與組裝根](subsystems/boot-and-config.md)。
- **Usage entry** — 以名稱引用某個 `Preset` ＋ 可選的逐欄 inline override 的一個 dict。存在 `agents.workspace_chat[]`/`kb_chat[]`/`infer_modules[]` 與 `kb.retrieval_llm`。catalog 在 build 時合併 `usage entry ◇ preset ◇ runner defaults`。`preset` 欄位**必填**。歸 [啟動與組裝根](subsystems/boot-and-config.md)。
- **AgentConfig** — FE picker 提供的一筆：`{name, model, system_prompt, suggestions, allowed_tools, env, sandbox_image, idle_timeout_seconds}`。純資料（msgspec.Struct），**不是** specstar 資源，存在 deploy config 而非 DB。歸 [Agent 執行時](subsystems/agent-runtime.md)。
- **AgentConfigCatalog** — runner-time 的 `AgentConfig` 目錄 ＋ 解析器：`list()`/`get(name)`/`default()`/`resolve(attached, template)`。解析串接 `attached → template → default` ＋ template 附錄合成。位於 `agent/config_catalog.py`。歸 [Agent 執行時](subsystems/agent-runtime.md)。
- **AgentToolContext** — agent 工具逐回合拿到的 context（sandbox、filestore、ask_kb bridge…）。RCA／KB 兩種 flavour 共用一個 dataclass，「flavour 不對」由可選欄位為 `None` 表示。歸 [Agent 執行時](subsystems/agent-runtime.md)。
- **AgentRunner** — 驅動一回合的 Protocol；catalog 重構後不管 picker，只在給定 context 下跑回合。實作：`LitellmAgentRunner`（prod）、`ScriptedAgentRunner`（測試）。歸 [Agent 執行時](subsystems/agent-runtime.md)。
- **args_recovery** — `agent/args_recovery.py` 的 FunctionTool wrap，在 invoke 時攔截 LiteLLM streaming 把多個 `tool_call.arguments` 併成 concat-JSON 的情況；剝出第一個 JSON 物件、丟出含「Extra data」的 `ValueError`，runner 的 `diagnose_error` 導向「一次只送一個 tool_call」retry。歸 [Agent 執行時](subsystems/agent-runtime.md)。

---

## KB 檢索

### 攝取與索引

- **Ingestor** — bytes → `SourceDoc`（status=indexing）→ chunk ＋ embed → `DocChunk`；store 與 slow index 都經 `asyncio.to_thread` 卸下事件迴圈。歸 [知識庫：攝取與索引](subsystems/kb-ingest-index.md)。
- **Embedder** — Protocol（`HashEmbedder` 測試用、`LitellmEmbedder` Ollama／hosted）；embedding 由我們算、raw vector 存在 `DocChunk`（cosine）。歸 [知識庫：攝取與索引](subsystems/kb-ingest-index.md)。
- **Chunker** — Protocol（`FixedTokenChunker`）；parser 產出整檔 Document，splitter 主掌粒度。歸 [知識庫：攝取與索引](subsystems/kb-ingest-index.md)。
- **SourceDoc** — 攝取後的文件紀錄（specstar 資源）。其 `id` 是不透明、無 slash 的 token（`encode_doc_id` ＝ 自然鍵 `{collection_id}/{path}`，把每個 `/` 換成 `∕`（U+2215）；**path-keyed，非 per-user**，**非** percent-encode）；**絕不要 parse**，從 record 的 `path`/`collection_id` 與 `created_by` meta 讀。歸 [知識庫：攝取與索引](subsystems/kb-ingest-index.md)。
- **DocChunk** — chunk ＋ raw 向量的紀錄（specstar 資源，`Vector` cosine）。歸 [知識庫：攝取與索引](subsystems/kb-ingest-index.md)。
- **IUploadCheck / UploadCheckRegistry** — 在 `Ingestor.store()` 最上方、建立任何 `SourceDoc` **之前**跑的可插拔同步守門（`abc.ABC`，介面／實作分離，比照 `IParser`）：`applies(filename, mime)` 選候選、`inspect(data)` 回 `UploadRejection｜None`。`UploadCheckRegistry.run()` 依註冊序跑、首個拒絕即丟 `UploadRejected`；預設 `bundled_upload_checks()`（Office OLE2 magic ＋ PDF `pypdf` 加密探測），可注入替換。歸 [知識庫：攝取與索引](subsystems/kb-ingest-index.md)。
- **UploadCheckHint** — check 可在瀏覽器跑的宣告式子集 `{id, extensions, forbid_magic_hex, message_key}`，FE 經 `GET /kb/upload-checks` 取得，用 leading-byte magic 先擋常見情況（加密 Office 檔）；伺服器再以同規則權威複跑，兩邊不會不一致。需真解析的 check（PDF）回 `None`、只在伺服器跑。歸 [知識庫：攝取與索引](subsystems/kb-ingest-index.md)。
- **UploadRejected** — check 拒絕時 `UploadCheckRegistry` 丟出的例外，攜 `UploadRejection {check_id, reason_code, message_key}`；上傳路由把它對映成 HTTP 422，FE 本地化成可關閉的「無法接受」清單。歸 [知識庫：攝取與索引](subsystems/kb-ingest-index.md)。

### 程式碼 Wiki（code-wiki，#281）

- **code-wiki / CodeWikiBuilder** — 針對 `git_url`（程式碼）collection、**讀原始碼**分層生成的 wiki：L0 逐檔卡片、L1 資料夾 roll-up、L2 架構／topics／index，每層只用下一層的摘要做**單次** LLM `collect`（非 agent loop），大 repo 也不爆 context；程式自己寫檔，繞開 #50「敘述而不寫」的失敗。位於 `kb/wiki/code_wiki.py`。歸 [知識庫：攝取與索引](subsystems/kb-ingest-index.md)。
- **outline（code outline）** — 單一原始檔的確定性 tree-sitter 符號骨架（top-level import／function／class／interface／enum，依原始順序），L0 檔案卡片配它再加一句 LLM 摘要；不支援副檔名或解析失敗時退化成空字串、絕不丟例外。`kb/wiki/code_outline.py`。歸 [知識庫：攝取與索引](subsystems/kb-ingest-index.md)。
- **file card / directory page / architecture page（L0／L1／L2）** — code wiki 的三層：L0 `/files/<path>.md` 逐檔、L1 `/dirs/<d>.md` 由子摘要 roll-up、L2 `/architecture.md` ＋ `/topics/<slug>.md`（≤6）＋ `/index.md`。每頁帶 `_SRC_MARKER`／`_IN_MARKER` input-hash，未變更則 rebuild 0 次 LLM 呼叫。歸 [知識庫：攝取與索引](subsystems/kb-ingest-index.md)。
- **CodeWikiBuildRun** — code-wiki 建置的 etag-CAS fan-out join 狀態（specstar 資源，resource id ＝ collection id）：`total`／`done`／`failed` batch、`finalized` exactly-once 閘、`status`（running｜done｜error）、`phase`（cards｜finalizing）。對應 #227 的 `IndexRun`。歸 [知識庫：攝取與索引](subsystems/kb-ingest-index.md)。
- **code_split / code_card / code_finalize** — 沿用既有 wiki JobType 跑 code-wiki fan-out 的三個 `WikiJobPayload.op`：`code_split` 規劃 batch ＋ 起 run ＋ 散出 card jobs；每個 `code_card` 建一批 L0 卡片並 CAS 記錄；`code_finalize` roll-up L1／L2 ＋ prune orphans，恰一次。歸 [背景工作與擴展](subsystems/jobs-and-scaling.md)。

### 檢索與 Agent

- **Retriever** — dense（specstar 原生向量查詢）＋ BM25 → RRF → MMR → parent-doc merge；接上 Llm 時可選 multi-query／HyDE／rerank。歸 [知識庫：檢索與 Agent](subsystems/kb-retrieval-agent.md)。
- **RRF / MMR / HyDE / multi-query** — 檢索管線的融合（Reciprocal Rank Fusion）、去冗（Maximal Marginal Relevance）、HyDE 假設文件探針、查詢改寫。歸 [知識庫：檢索與 Agent](subsystems/kb-retrieval-agent.md)。
- **Enhancements** — 逐次搜尋的 override 旋鈕 dataclass `{expand, hyde, rerank}`（`kb/retriever.py`）：`expand` ＝ 替代查詢數（0 ＝ 關）、`hyde` ＝ 假設文件探針數、`rerank` ＝ 融合後 LLM rerank；每欄 `None` ＝ 繼承 operator 預設。歸 [知識庫：檢索與 Agent](subsystems/kb-retrieval-agent.md)。
- **EnhancementSettings** — operator 級預設 ＋ 上限（`kb.retrieval.enhancements`）；每旋鈕 `{default, max}`。Bundled 偏輕：`expand=1, hyde=0, rerank=true`。歸 [知識庫：檢索與 Agent](subsystems/kb-retrieval-agent.md)。
- **Resolution cascade（enhancement）** — LLM 工具參數 `kb_search(query, expand?, hyde?, rerank?)` 勝過 Python caller（`Retriever.search(..., enhancements=...)` ＋ `AgentToolContext.kb_enhancements`）勝過 operator `default`；`max` 夾住結果（int floor 0 / cap max；bool ＝ `raw AND max`，`max=False` 為硬殺）。歸 [知識庫：檢索與 Agent](subsystems/kb-retrieval-agent.md)。
- **KB agent** — 與 RCA 同一個 `AgentRunner`，搭 KB flavour 的 `AgentToolContext`（retriever ＋ collection_ids、無 sandbox）＋ `kb_search` 工具，跑真正的 agent loop。歸 [知識庫：檢索與 Agent](subsystems/kb-retrieval-agent.md)。
- **kb_search** — KB agent 自己的檢索**葉子**工具（向量搜尋 → 編號 `[n]` 段落）；只授予「本身就是 KB agent」者。歸 [知識庫：檢索與 Agent](subsystems/kb-retrieval-agent.md)。
- **ask_knowledge_base** — 給每個*其他* app agent（RCA／playground／topic-hub）的 consumer 介面工具：把整個問題委派給 `kb_chat` sub-agent（context 隔離），回傳已合成、附引用的答案；**絕不**改授 `kb_search`。歸 [知識庫：檢索與 Agent](subsystems/kb-retrieval-agent.md)。
- **lookup_glossary** — 便宜、確定性、精確 key 的 context-card 查詢（無 LLM、無 retriever），任何 app 可直接授予。歸 [知識庫：檢索與 Agent](subsystems/kb-retrieval-agent.md)。
- **ContextCard** — 掛在 Collection 上的輕量確定性詞彙卡（specstar Struct，多對多 keys；`norm_keys.contains` 精確查詢），與 `kb_search` 並存。歸 [知識庫：檢索與 Agent](subsystems/kb-retrieval-agent.md)。

---

## Sandbox

- **Sandbox（Protocol）** — agent `exec` 工具首次使用時**惰性建立**；純檔案操作走 FileStore、不會起 sandbox。歸 [Sandbox、FileStore 與同步](subsystems/sandbox-and-filestore.md)。
- **MockSandbox** — in-memory，測試用。歸 [Sandbox、FileStore 與同步](subsystems/sandbox-and-filestore.md)。
- **LocalProcessSandbox** — subprocess ＋ temp dir，VM 部署預設（`DockerSandbox` 已 DEPRECATED → sandbox-host）。歸 [Sandbox、FileStore 與同步](subsystems/sandbox-and-filestore.md)。
- **FileStore（Protocol）** — 純檔案存取接縫；`SpecstarFileStore` 把每個 workspace 存成 specstar 內的 blob。歸 [Sandbox、FileStore 與同步](subsystems/sandbox-and-filestore.md)。
- **InvestigationRegistry** — 只擁有 **sandbox** 生命週期（RCA）；turn／cancel／SSE 不在此而在 `ChatTurnEngine`。歸 [Sandbox、FileStore 與同步](subsystems/sandbox-and-filestore.md)。
- **sandbox-host** — 承載 toolchain 的 sandbox host 映像（HTTP host 會忽略 `SandboxSpec.image`）；office／make_deck 等工具鏈裝在此。歸 [工具套件與 Sandbox Host](subsystems/tooling-and-sandbox-host.md)。
- **tool packages（sample-tools/）** — agent 可呼叫的工具套件，**只能絕對 import**（ruff TID252 強制）；例如 `make_deck`、office、`read_image`。歸 [工具套件與 Sandbox Host](subsystems/tooling-and-sandbox-host.md)。

---

## Workflow

- **Workflow** — API 觸發的 headless 工作流；backend 編排 ＋ 以檔案系統為 journal（filesystem artifacts ＋ input-hash，非抽象 replay）；可用 Python `run()` 或使用者共筆的宣告式 `workflow.json` DSL（#323，見下）；profile 級。歸 [Workflow 引擎](subsystems/workflow-engine.md)。
- **decision／action split** — workflow 把判斷（decision）與副作用（action）分開；produce → review → commit。歸 [Workflow 引擎](subsystems/workflow-engine.md)。
- **human_gate** — produce → review → commit 流程中的人類關卡（v1）；以釘頂 `WorkflowDecisionCard` 呈現。歸 [Workflow 引擎](subsystems/workflow-engine.md)。
- **journal（`/.workflow/<workflow_id>/`）** — workflow 的 `step_*` 日誌資料夾，移出 item root；經 `WorkflowHandle.journal_dir` 串接。歸 [Workflow 引擎](subsystems/workflow-engine.md)。
- **steering** — 對話式調整（free-text → LLM 計畫：改 inputs ＋ 失效 steps → 人類確認影響範圍 → 確定性套用 → 同一個 run 增量續跑）。歸 [Workflow 引擎](subsystems/workflow-engine.md)。
- **User-authored workflow（降權 DSL）** — 與 AI 共同設計、存成資料（一份 `workflow.json`）而非 Python 的工作流；可信賴的 interpreter（`build_run`）在既有 step primitives 上跑它，故 **API 內不執行使用者程式碼**。是 user skill 的工作流類比（#298／#323）。歸 [Workflow 引擎](subsystems/workflow-engine.md)。
- **workflow.json（DSL schema）** — 非圖靈完備的宣告式定義：有序 steps，`type ∈ agent／sandbox／gate／capability／map`，加上確定性 `{x}`／`{x.field}` 字串內插（無 eval）。由 `parse_def` 解析成 `WorkflowDef`。歸 [Workflow 引擎](subsystems/workflow-engine.md)。
- **Capability（workflow step）** — 可靠、冪等的副作用 step（`ingest_to_collection` 或 `upsert_context_card`），在擷取到的使用者 authz 下執行——使用者 DSL 唯一的副作用路徑，因為使用者的 sandbox step 是純計算（無 credential）。歸 [Workflow 引擎](subsystems/workflow-engine.md)。
- **Workspace workflow（`.workflows/`）** — 存於 FileStore `<workspace>/.workflows/<id>.json` 的使用者共筆工作流：item 本地、即時讀取、可手改／下載，且**遮蔽**同 id 的 package 工作流（Q5）。與 run journal `/.workflow/<workflow_id>/` 不同。歸 [Workflow 引擎](subsystems/workflow-engine.md)。

---

## 資料層 specstar

- **specstar** — spec-driven 的 FastAPI 框架，本平台預設後端；Workspace／AgentConfig（非）／Conversation／KB 各資源 auto-CRUD。**永遠新建一個 `SpecStar()` 實例**，不要用 module-level singleton。歸 [資料層（specstar）](subsystems/data-layer.md)。
- **add_model / indexed_fields** — 註冊資源 model ＋ 宣告索引欄位；要過濾／排序某欄就索引它並用 QB 查（`.sort`/`.limit`），不要 fetch-all ＋ Python filter。歸 [資料層（specstar）](subsystems/data-layer.md)。
- **Schema / migrate / MigrateRouteTemplate** — 把新索引 backfill 到舊 row：`Schema("vN").step(None, _reindex_only, ...)` ＋ operator 跑 `POST /{model}/migrate/execute`（`rm.migrate` 重新萃取 `indexed_data`）；**別手刻** reindex 迴圈。歸 [資料層（specstar）](subsystems/data-layer.md)。
- **exp_aggregate_by / .contains** — 頁面級 count/sum 經 `exp_aggregate_by(..., query=...)` 限定到該頁 ids（非全域 group-by）；`.contains` 是 membership **filter** 非 group-by，在 indexed `list[str]` 上是 EXACT 元素成員（自 specstar 0.11.9）。歸 [資料層（specstar）](subsystems/data-layer.md)。
- **autocrud** — specstar 從 `/openapi.json` codegen FE client（`ResourceField` 形狀）；本平台改在 runtime 服務精簡子集，故 FE 無需 codegen。歸 [資料層（specstar）](subsystems/data-layer.md)。
- **Conversation** — 一張共用對話表，`item_id: str` 不透明 ＋ 索引；刪除清理是 per-App on-delete event_handler。歸 [資料層（specstar）](subsystems/data-layer.md)。

---

## 其他橫切

- **ChatTurnEngine** — RCA workspace ＋ KB chat 共用的單一回合引擎（`api/turns.py`）：per-conversation lock、單一可取消 in-flight turn（新訊息取消前一個）、`_drive` pump、SSE `gen()` 把事件 reduce 成中性 `TurnMessage`、`cancel()`/`forget()`。別逐 surface 重刻 turn／cancel／SSE。歸 [API 與回合引擎](subsystems/api-and-turns.md)。
- **TurnMessage** — `ChatTurnEngine` 產出的中性訊息；每個 surface 用 `on_complete` 把它對映到自家 model（`Message`／`KbMessage`）。歸 [API 與回合引擎](subsystems/api-and-turns.md)。
- **SSE event schema** — `api/events.py` 定義、`web/src/events.ts` 鏡射；新增事件型別要兩邊同步。KB chat 串同一套事件。歸 [API 與回合引擎](subsystems/api-and-turns.md)。
- **JobType** — 背景工作型別（index／wiki／card-gen／sanity）；一個 worker pod 阻塞消費**一個** JobType（`consume_until_stopped`），各自在自己的 k8s HPA 下擴展。歸 [背景工作與擴展](subsystems/jobs-and-scaling.md)。
- **build_coordinators** — 唯一、FastAPI-free 的 job coordinator 組裝根（＋ `build_ingestor`/`resolve_wiki_config`），`create_app` 與 standalone worker（`python -m workspace_app.worker <jobtype>`）共用；新 coordinator 加在這裡，別 inline 進 `create_app`。歸 [背景工作與擴展](subsystems/jobs-and-scaling.md)。
- **run_consumers** — API 是否 in-process 消費的開關（預設 `true` ＝ all-in-one；`false` ＝ API 純 producer，只 `add_model`／`enqueue` 不 `start_consuming`）。非 queue sweepers 永遠留在 API、不受此 gate。歸 [背景工作與擴展](subsystems/jobs-and-scaling.md)。
- **TanStack Query** — FE 資料層：GET-style 讀走 `useQuery`（keys 在 `web/src/api/queryKeys.ts`），寫走 `useMutation` ＋ `invalidateQueries`；SSE 仍 imperative（`useAgent`/`useKbChat`），但初始 hydration 是 `useQuery`。歸 [前端](subsystems/frontend.md)。
- **Platform Help（collection）** — well-known 系統 KB collection（name=`Platform Help`），每次開機從套件內 `src/workspace_app/help_content/`（`CHANGELOG.md` ＋ `getting-started.md`）seed。owner 為幻影 `system` 使用者；公開可讀／搜尋／chat，但寫入鎖到 owner ＋ superuser（沿用 #262 Permission model）。撐起 `/help` 頁（`GET /api/help` 回 `HelpInfo {collection_id, documents}`）；#281 的 code-wiki 未來規劃餵進同一個 collection（目前尚未接線）。歸 [平台服務](subsystems/platform-services.md)。
