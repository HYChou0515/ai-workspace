# 開發者指南（Development Guide）

給要在這個 codebase 上開發的人:環境、開發流程、品質 gate、**必須遵守的規則**,以及幾個最
常見的「怎麼加一個 X」的步驟。這是規則與慣例的單一來源。

> 對照閱讀:[`CLAUDE.md`](https://github.com/HYChou0515/ai-workspace/blob/master/CLAUDE.md)(架構圖 + 指令,權威來源)、
> [`CONTEXT.md`](https://github.com/HYChou0515/ai-workspace/blob/master/CONTEXT.md)(領域詞彙,用語請完全照它)、
> [architecture.md](architecture.md)、[contract.md](contract.md)、[deployment.md](deployment.md)、
> [adding-an-app.md](adding-an-app.md)、[workflows-authoring.md](workflows-authoring.md)。

---

## 1. 環境與指令

**後端**(Python 3.12,uv 管理):

```bash
uv sync --all-extras                                       # 安裝(必加 --all-extras)
uv run python -m workspace_app                              # 跑 app + SPA(127.0.0.1:8000)
uv run pytest tests/path/test_x.py::test_name              # 跑單一測試
uv run ruff check && uv run ruff format --check             # lint + 格式
uv run ty check                                             # 型別檢查(整個專案 — 見 §3)
```

> 用 `uv sync --all-extras`,不要只 `uv sync`——`process-sandbox` extra(pandera / scipy /
> scikit-learn / seaborn)是 tabular parser + 資料分析工具需要的,少了會讓 `test_infer_modules` 紅燈。

**前端**(React + Vite,在 `web/`):

```bash
cd web && pnpm install
pnpm run dev          # 開發伺服器(5173,proxy 後端)
pnpm run build        # 打包 web/dist(後端自動掛載)
pnpm run typecheck
pnpm exec vitest run  # 跑測試
```

> **工具鏈固定,不要換掉**:`uv`(不用 pip/poetry/pdm)· `pytest`(+ `pytest-asyncio`)·
> `ruff` 同時做 lint 與 format(不用 black/flake8/isort)· `ty`(Astral 的檢查器,不用 mypy/pyright)·
> **`coverage.py` 直接用——絕不加 `pytest-cov`**。Python 工具拿不定主意時,先看 Astral 有沒有出。

---

## 2. 開發流程:規劃 → 測試先行 → 出貨

1. **先規劃**:新功能、bug report 一律先 **`/grill-me`**——把計畫壓力測試過、把每個分支的開放
   問題解掉,再寫任何程式。
2. **測試先行實作**:計畫清楚後走 **`/tdd`**(red → green → refactor):先寫一個描述「行為」的
   失敗測試(透過公開介面,不綁實作細節)→ 寫剛好讓它過的最小程式 → 綠燈下重構。
   **前端也一樣**(vitest)——`docs/plan-frontend.md` 那句「不需要測試」**不是** TDD 的豁免令。
3. **Phase 用扁平整數序**:`Phase 1`、`Phase 2`…(`P1`、`P2`…)。**不要**字母子階段(`Phase 1a`/`1b`)。
   「Phase 1」代表 Phase 1 要被*做完*;要切出去的工作就變成下一個整數,不是字母後綴。

> 好的測試讀起來像規格、能撐過重構;壞的測試綁內部結構,改名就壞。

---

## 3. 品質 Gate

**權威 gate(全套測試 + 100% 覆蓋率)**:

```bash
uv run coverage run -m pytest && uv run coverage combine && uv run coverage report --fail-under=100
```

- 後端 **100% 覆蓋率**是標準。`coverage` 跑在 **parallel 模式**(CI 的 xdist worker 各自記錄),
  所以 `report` 前**一定要先 `coverage combine`**——即使是本機序列跑也一樣。
- **CI 只跑 unit 測試**:`pytest -m "not integration" -n auto`(約 97% 覆蓋率,**不**卡 100%)。
  **integration** 測試(`@pytest.mark.integration`:真 docker / subprocess sandbox / jupyter kernel /
  uv / ollama)會塞爆 CI runner 硬碟、要約 90 分鐘,所以只在上面那個本機全套跑——100% gate 就在那裡。
- **迭代時跑得快,gate 跑一次**:迭代中只跑改到的行為的**目標測試** + `ruff check` /
  `ruff format --check` / `ty check`;全套 + 100% gate 在一批工作結束時(收尾點)**跑一次**,不是每次改都跑。
- **gate 不要用 pipe 遮住失敗**:`pytest … | tail` 回的是 *tail* 的 exit code(0),紅的會看起來像綠的,
  後面 `&& coverage report` 還照跑。要 `pytest; echo $?`(不接 pipe)、加 `-rf` 列出失敗 id、讀
  `N failed, M passed` 那行。背景跑的話讀整份輸出檔,別信 wrapper 的「exit 0」。
- **ty 檢查整個專案**:`uv run ty check` **不指定檔案**——CI 連 `tests/` 都檢。曾經 file-scoped
  `ty check <檔案>` 本機過、CI 卻在一個 test helper 上掛掉。回傳 callable 的 helper 要精確標
  `Callable[[...], ret]`,絕不用 `object`。

---

## 4. 必須遵守的規則

### 4.1 語言與溝通
- **回覆使用者用繁體中文(台灣用語)**。
- **程式碼、識別字、commit、檔案內容用英文**,除非使用者明確要求別的(本檔與 `CONTEXT.md`/
  其他 `docs/` 同樣以中文撰寫,是這份面向使用者的文件的既定例外)。

### 4.2 Python 程式風格
- **新的可抽換介面用 `abc.ABC` + `@abstractmethod`,不要 `typing.Protocol`**。介面名加 `I` 前綴
  (如 `IMonitor`),具體實作平實命名(`InMemoryMonitor`);**介面與實作分檔**。既有那批 Protocol
  層(Sandbox / FileStore / AgentRunner / Embedder …)**不要整批遷移**——這條只套用在新程式。
- **API 端點回傳有型別的 pydantic response model,不要裸 `dict`**——為了 OpenAPI schema、驗證、
  與 FE 型別對齊。標 `-> SomeOut` / `-> list[SomeOut]`,model 與 route 放一起,FE 型別同步。
  機會式轉換(改到的端點才轉),不要大爆改。
- **specstar struct 欄位用 `dict[str, Any]`,不要 `dict[str, object]`**(`object` 會壞掉 JSON-schema
  產生)。`resource.data` 用 `assert isinstance(...)` 收斂給 `ty`(coverage 乾淨)。
- **`sample-tools/` 底下的工具包只用絕對 import**——不准 `.`/`..`,包含 `__init__.py` re-export 與
  `cli.py`。由 ruff `TID252` + 各包 pyproject 的 `ban-relative-imports = "all"` 強制。改完工具包原始碼
  要重跑 `uv run python scripts/prebuild_tools.py`。(`src/workspace_app` 維持既有的相對 import 風格。)

### 4.3 specstar 持久化
specstar([github.com/HYChou0515/specstar](https://github.com/HYChou0515/specstar))是預設後端——
把東西建模成一級 resource,不要包一層。

- **永遠用 `SpecStar()` 建新實例**,不要 module-level `specstar.spec` singleton(測試隔離)。
- **每樣東西建模成一級 specstar resource**,靠它的可抽換儲存。使用者完全接受 specstar 自動長出
  CRUD 路由——**不要為了藏路由去包 specstar**、也不要把它當 blob KV(一個 workspace 一個大 blob 的
  `SpecstarFileStore` 是反例;一個 thing 一個 resource 才對)。
- **絕不重定義框架 metadata**:`created_time`/`updated_time`/`updated_by`/版本/刪除/索引值都是自動追蹤的,
  從 `.meta` / `.info` 讀。(例外:list 欄位裡的子物件不是自己的 resource,所以自帶 `created_at`。)
- **用 `resource_id`,絕不用 `uid`**。`uid` 是內部私有屬性;`resource_id` 是公開、已全域唯一的識別字
  (不必自己組 `{type}/{id}`)。
- **要過濾/排序某 Struct data 欄位就先索引它**(`add_model(indexed_fields=[...])`),用 query builder 查
  (`.sort`/`.limit`/`.offset`/`.page`、`count_resources`)。**絕不 fetch-all + Python 過濾/排序**。
  「最近 N 筆」把 `.sort("-field").limit(n)` 推進 query。
- **分頁/清單的聚合要 scoped**:`exp_aggregate_by(..., query=…)` 綁到該頁的 ids 或該 collection——
  不要為了查一頁做全域 group-by。`.contains` 是成員*過濾*,不是 group-by(產不出 per-element 計數)。
  在**已索引的 `list[str]`** 欄位上,`.contains` **自 specstar 0.11.9 起在所有 backend 都是精確成員判斷**
  (`"m4"` ≠ keyed `"m40"` 的 card)。欄位必須同時留在 `indexed_fields` 且標註成 `list[...]`,否則 SQL
  `.contains` 會悄悄退化成 substring `LIKE`(in-memory 測試抓不到 Postgres-only 的退化)。
- **對會變動的 list 做穩定 offset 分頁,用 `created_time desc, resource_id asc` 排序**——兩者都不可變。
  `updated_time` 是錯的分頁 key(每次 re-index 都會跳 → 重複/遺漏)。
- **回填新索引用 migrate API,不要手刻 reindex loop**。pre-Schema 的舊列註冊
  `Schema("vN").step(None, _reindex_only, source_type=Model)`,再由 operator 跑 migrate 路由
  `POST /{model}/migrate/execute`(`MigrateRouteTemplate`,在 `make_spec` 全域註冊)。讀取會 lazy-migrate
  record 但不會更新 meta `indexed_data`,所以在明確 migrate 前,聚合會少算舊列。
- **event handler**:`rm.patch` 同時發 patch + update;`rm.update` 只發 update。要對「使用者編輯」反應就
  scope 到 `on_success(ResourceAction.patch)`,避免在 worker 的 update 上打轉。`add_model` 之後用
  `rm.event_handlers.extend(...)` 接線。`on_success` 在 commit 後同步跑,且**必須吞例外**(raise → HTTP 500)。
- **手刻 ops/infra 前先找 specstar 內建**(route template、migrate、query helper)。對 specstar 有疑問就
  **直接發到它的 GitHub Discussions**(`gh`,`HYChou0515/specstar` 的 Q&A 分類),不要留一個過水的本地 md。

### 4.4 LLM 與 agent
- **每個 LLM 呼叫都必須 streaming**。我們的程式裡任何地方都不准非串流的 `chat.complete`(大大傷害
  observability)。LLM 介面是 streaming-only;呼叫端自己累加串流,並把每個 chunk 轉發給進度 sink,
  讓 thinking 即時可見。
- **AI 是可抽換的外部依賴——ai-workspace 永遠不需要 hosted AI**。透過既有接縫拿模型(AgentRunner
  Protocol + usage-reference 設定 → `resolve_llm_chain` → LiteLLM)。絕不寫死模型、也不假設 hosted 或 local。
  功能可以要求某*能力*(如 multimodal)而不要求*hosting*——加一個 config usage-reference → 具名 preset,
  只強加能力前置條件(不滿足就 fail-loud + live-check)。
- **範例/本地預設用 LiteLLM + 小型本地 Qwen via Ollama**(如 `ollama_chat/qwen3:14b`),不是 hosted
  GPT/Claude。用 `ollama_chat/` 前綴(純 `ollama/` 會悄悄丟掉 tool calling)。把 7B/14B 的 tool-calling
  脆弱性當一級議題(malformed tool call → retry-with-feedback),不是事後補。
- **LLM 功能的 live check 是 Definition of Done 的一部分**。fake-LLM 測試只驗*我們的*程式,不驗*模型*能
  不能做這件事——早點對真的本地模型驗(宣告完成前先跑一次 live repro)。功能要附一個 canned sanity check。
  除錯 = replay(context 快照 + LLM → 原始輸出,**不執行工具、零副作用**)。
- **有文獻/研究背書的決策,引用要同時放進程式註解與 PR 內文**——不只在聊天裡——讓理由能從程式追到、
  也能在 PR 審到。
- **`kb_search` vs `ask_knowledge_base` — leaf vs 消費者介面,不要合併**。`kb_search`(以及
  `search_wiki`/`read_source`)只給「本身就是 KB/wiki agent」的 agent。其他 app agent 一律給
  **`ask_knowledge_base`**(委派給 `kb_chat` 子代理做 context 隔離)。`lookup_glossary` 是例外——便宜、
  確定性、不用 LLM/retriever——任何 app 可直接給。

### 4.5 KB 與 chunking
- **parser 吐整檔 Document;切分(`DispatchSplitter`)那層決定粒度**。chunk 大小選擇(每 chunk 幾列、
  sentence token/overlap)是**可調超參數**,不是設計決策——原檔一定有存、每個 chunk 都 Ref 它的 SourceDoc,
  所以粒度可以靠 reindex 還原。不要 grill chunk 大小;**要** grill 文字形狀(embedder 看到什麼)、VLM prompt
  設計、多頁/多 sheet 處理。
- **VLM 功能用「有文字的圖」來探測/驗證**(小 VLM 對無特徵合成圖會幻覺,還可能 GGML crash);VLM prompt
  模板要給明確的空區塊出口。

### 4.6 前端
- **`web/` 新程式走 TDD(vitest)**(見 §2)。對既有無測試程式補測沒問題,但一旦你為了新行為去*改*它,
  新行為就走 TDD。
- **資料層是 TanStack Query**。GET 讀走 `useQuery`(key 在 `api/queryKeys.ts`),寫走 `useMutation` +
  `invalidateQueries`。SSE 維持 imperative(`useAgent`/`useKbChat`),初始 hydration 用 `useQuery`。登入者用
  `useCurrentUser()`,不是寫死常數。
- **SSE event schema 要同步**:`api/events.py` ↔ `web/src/events.ts`——兩邊都加型別、更新 reducer
  (`agentLog.ts`),加事件時也更新 contract 文件(見 §6)。
- **FE 絕不寫死 App slug**(如 `manifest.slug === 'topic-hub'`)。per-App 行為是 manifest/template 驅動——
  在 `src/workspace_app/apps/<slug>/app.json` 宣告欄位(`Layout`/`FunctionToggles`,如 `primary_surface`),
  FE 型別(`web/src/api/types.ts`)同步,給預設值讓既有 App 不受影響,FE 判 `manifest.layout.<field>`。
- **`design_handoff_rca_3.0/` 是參考,不是權威**。可參考它的外觀/版面/互動,但它和雙方談定(grill 過)的
  設計與資料模型衝突時,以談定的為準。**不要改它**(使用者自己維護)。*資料*衝突不是重新設計*外殼/chrome*
  的許可證——忠實照搬它的容器型態(modal 就做 modal)與版面。
- **UI 文案描述使用者的動作/結果,不露內部細節**。使用者看得到的字串不准出現檔案格式、mime type、子系統
  名稱——那些放程式註解。新字串走既有 i18n(`lib/i18n.tsx` + `useT`,zh-TW + en),用語要去術語化。

### 4.7 工具與 sandbox
- Sandbox Protocol 有 `MockSandbox`(測試)、`LocalProcessSandbox`、`HttpSandbox`(自架 host,獨立 pod)。
  **`DockerSandbox` 已棄用** → sandbox-host。office/資料庫等套件放 python-stack venv carrier(與 sandbox-host
  image),不是 Dockerfile。
- sandbox 由 agent 的 `exec` 工具**首次使用時才 lazy 建立**;純檔案操作走 FileStore,不會起 sandbox。
- RCA 範例/seed 內容請選有用的產物(SPC 分析、Pareto、column summary、report drafting),避免 legacy 過時範本檔。

---

## 5. 專案結構

```
src/workspace_app/
  api/         create_app、SSE 端點、turns.py（ChatTurnEngine,RCA+KB 共用 turn/cancel/SSE）、
               litellm_runner.py、kb_routes.py、kb_chat_routes.py、events.py（AgentEvent/CellEvent）
  agent/       AgentToolContext（雙形態 RCA/KB）、tools.py（exec/read/write/list_files…、kb_search、
               ask_knowledge_base、lookup_glossary、read_image、make_deck…）
  apps/        多 App 平台：manifest.py、catalog.py、registry.py、resolve.py、seeding.py、profiles.py、
               shared_skills.py、_base.md（共用 preamble）、rca/、playground/、topic-hub/、_template/
  workflow/    headless workflow 引擎 + CLI（new/check）
  kb/          chunker、embedder、ingest、retriever（hybrid）、fusion/bm25/merge、query（multi-query/HyDE）、
               rerank、citations、context_cards、llm、agent + prompts
  failover/    priority-list 模型 fallback 核心 + adapters（FallbackLlm/Vlm/Model）
  files/       WorkspaceFile（Binary）+ streaming + zip_download
  sandbox/     Protocol + Mock / LocalProcess / Http（DockerSandbox 已棄用）
  filestore/   Protocol + Memory / Specstar
  sync/        SandboxSync（restore/flush/reverse）
  resources/   msgspec.Struct（AgentConfig / Conversation；KB：Collection / SourceDoc / DocChunk / KbChat …）
  monitor/、observability/、health/、perm/、users/、worker/、tooling/、config/、
  coordinators.py、factories.py

web/src/
  pages/       各 App workspace（ItemChatShell …）、KB（KbHome/KbChatPanel/KbCollectionsPage/KbDocIde …）、
               Diagnostics
  components/   AgentEntryView（RCA + KB 共用 log 渲染：reasoning / tool cards / metrics）、Icon …
  api/          TanStack Query client（queryKeys.ts / queryClient.ts）、kb.ts、types.ts
  renderers/    Markdown / Text / FileView / notebook / report
  hooks/        useAgent、useKbChat、useEditorGroups、fileBuffer …
  lib/          i18n.tsx（LocaleProvider / useT）…
  routes/       react-router-dom 路由
  events.ts     AgentEvent/CellEvent（鏡像後端 events.py）

（其他頂層）sandbox-host/  獨立的 HTTP sandbox host（自己的 pyproject + uv.lock,wire-contract only）
            sample-tools/  agent 工具包（絕對 import；prebuild_tools.py）
            sample-skills/ 可載入 skill 範例
```

---

## 6. 怎麼新增一個 SSE 事件型別

事件要跨 BE→FE 同步:

1. **後端 `api/events.py`**:加 `@dataclass(frozen=True)`,含 `type: Literal["…"] = "…"`,
   並加進 `AgentEvent`(或 `CellEvent`)union。
2. **發送點**:在 `litellm_runner.py`(或對應產生器)yield 它。
3. **前端 `web/src/events.ts`**:加對應 `type`,並加進 `AgentEvent` union;若為終止事件,更新 `isTerminal`。
4. **前端 reducer `pages/investigation/agentLog.ts`**:在 `reduceAgent` 加 `case`,把事件折進狀態;
   先寫 vitest(`agentLog.test.ts`)紅燈再實作。
5. **渲染**:在共用的 `AgentEntryView`(RCA + KB chat 都用它)對應位置顯示。
6. **契約文件**:更新 [contract.md](contract.md) §3 表格。

---

## 7. 怎麼新增一個 agent 工具

1. 在 `agent/tools.py` 寫 `async def my_tool_impl(ctx: RunContextWrapper[AgentToolContext], ...)`,
   透過 `ctx.context.filestore` / `ctx.context.sandbox` / `ctx.context.retriever` 操作。
2. 加進 `_IMPLS` dict(key 是工具名)。`build_tools(allowed)` 會自動包成 `FunctionTool`;`allowed=None`
   回傳 `_WORKSPACE_TOOLS`(只是 fallback 預設集)。
3. 若工具有即時輸出需求,沿用 `ctx.context.on_exec_output` 那套 sink(見 `exec_impl`)。
4. 在 `tests/agent/test_tools.py` 寫測試(用 `MockSandbox`/`SpecstarFileStore` 的 `ctx` fixture)。

> **要讓某 App(如 RCA)預設拿到新工具,必須加進該 App 的 `app.json` `tools`,不是只加進
> `_WORKSPACE_TOOLS`**——後者只是沒設定時的 fallback。這是 #275(`lookup_user`)踩到的雷。
>
> **`AgentToolContext` 是雙形態**:RCA turn 帶 `sandbox/filestore/sync/item_id`;KB turn 帶
> `retriever/collection_ids`、沒有 sandbox。所以這些欄位都是 optional——RCA 工具開頭斷言取出
> filestore/item_id,KB 的 `kb_search` 斷言 retriever。需要 retriever 的工具(如 `kb_search`)只給
> KB agent,**不要**給其他 App(見 §4.4)。

---

## 8. 怎麼新增一個檔案 renderer

前端依副檔名/檔名挑 renderer(`web/src/renderers/`):

1. 在 `renderers/` 寫元件,接 `{ itemId, path }`(或對應 props),用 `useFileBuffer`/`useFileContent` 取內容。
2. 在 renderer 的分派表登記副檔名/規則。
3. Markdown 類請沿用既有設定:`remarkPlugins={[remarkGfm, remarkMath]}` `rehypePlugins={[rehypeKatex]}`
   (LaTeX/GFM 一致)。
4. 二進位/圖片走 `FileView`;所有檔案都應可開啟編輯(含 binary)。
5. 寫 vitest。

> agent 寫檔的慣例(FE renderer 依賴)見 [contract.md](contract.md),例如 `/report.vN.md`
> (最大 N 為現行版本)。

---

## 9. 怎麼新增一個 App 或 profile

App 是多 App 平台的一級單位。完整步驟見 [adding-an-app.md](adding-an-app.md),詞彙定義見
[`CONTEXT.md`](https://github.com/HYChou0515/ai-workspace/blob/master/CONTEXT.md)「Apps & work items」。要點:

- **App** = 一個 in-code 目錄 `src/workspace_app/apps/<slug>/` = `app.json`(identity + function +
  agent + layout)+ `model.py`(它的 `WorkItemBase` 子類 Struct)+ `prompts/` + `profiles/`。
  `tools[]` 與 function toggle(`workspace`/`sandbox`/`terminal`)不一致是**啟動時硬錯誤**。
- **profile** = App 裡的具名起始內容包 `apps/<slug>/profiles/<name>/`:seed 檔 + `_prompt.md`(prompt 附錄,
  只描述*這個 profile* 的起始檔)+ `.skill/` + `_profile.json`(title/description/suggestions/tools(⊆ app.tools)/
  presets(⊆ app.picker)/default_preset)。建立 item 時選擇。
- 新增 App 後務必跑 `uv run python -m workspace_app.workflow check`(若有 workflow)、起一次 app 確認
  manifest/profile 解析無誤。

> 跨 App 的共用慣例(`/report.vN.md` 版本規則、notebook 由 user 執行…)寫進 base prompt,不要寫進 profile 附錄。

---

## 10. 怎麼換／新增 KB 的 chunker / embedder / 檢索步驟

KB 的可抽換點都在 `src/workspace_app/kb/`,皆為小介面:

- **Chunker**(`chunker.py`):`chunk(text) -> list[Chunk]`。新切法實作介面即可;測試比對
  `Chunk.start/end` 為對 canonical text 的字元 offset。
- **Embedder**(`embedder.py`):`dim` + `embed_documents` + `embed_query`。繼承 `_PrefixedEmbedder` 只需寫
  `_embed` 與 `dim`(prefix 已處理);live 呼叫標 `# pragma: no cover`。注入靠 `create_app(kb_embedder=...)`。
- **檢索增強**(`query.py` / `rerank.py`,靠 `Llm` 介面):multi-query、HyDE、rerank 都是「prompt 純函式 +
  注入 `Llm`」——parsing 寫純測試(fake `Llm`),整合進 `Retriever.search`,並用 `# pragma: no cover` 圈住
  live model 路徑。記得 LLM 介面是 streaming-only(見 §4.4)。
- **Retriever 管線**(`retriever.py`):dense(specstar 原生向量查詢)+ BM25 → `fusion.py` RRF → MMR →
  `merge.py` parent-doc 合併。加新訊號就多疊一個 ranked list 進 RRF。

> 攝取是「`store`(快、同步、`status=indexing`)+ `index`(慢、背景)」兩段——慢的嵌入別放進上傳 request;
> 測試可直接呼叫同步版。引用解析(`[n]`→`Citation`)在 `citations.py`,是純函式。

---

## 11. 平台說明頁（Help，#230）的內容怎麼更新

`/help` 頁的「使用說明 + 更新紀錄（release note）」就是一個系統 KB collection
「Platform Help」的內容，AI 問答直接 `kb_search` 它。

- **改內容 = 改 repo**：編輯 `src/workspace_app/kb/help_content/*.md`
  （`getting-started.md` = 使用說明、`CHANGELOG.md` = 更新紀錄；檔名 `CHANGELOG.md`
  會被標成 `release_notes`，其餘為 `guide`）。內容隨 wheel 出貨,**每次開機
  idempotent upsert**（相同 bytes 為 no-op）——repo 是唯一真相,UI 上手改的會被下次
  開機覆蓋。新增一份指南只要丟一個 `.md` 進去即可。
- **權限**：collection 公開可讀可搜,但寫入鎖給 owner（`system`）+ superuser
  （`settings.server.superusers`),用既有 #262 `Permission`,**沒有新權限碼**。
  機制見 `kb/help_collection.py`,端點見 `api/help_routes.py`。
- **前向相容 #281**：之後讓 AI 讀原始碼生成 wiki 會餵進**同一個** collection,
  屆時 source doc 是完整 codebase 而非單一 `CHANGELOG.md`。
- **尚未做（deferred）**：help 專屬「人格」system prompt——目前無 per-collection
  的 chat-prompt 追加掛點,故沿用預設 KB agent prompt（知識庫即 help 內容,問答+引用
  已足夠）；要加人格時再評估注入機制,別為此硬塞新 preset。

---

## 12. Git 與 PR 慣例

- **本機 commit 是常態節奏**。**不要主動提議/詢問** push 或開 PR——但使用者明確要求時就照做。在預設分支
  (`master`)上要先開 branch。
- **stacked PR 以它的 parent branch 為 base**,不是 master,讓 diff 維持真正的 delta。先確認拓樸
  (`git log base..branch`、`merge-base --is-ancestor`);PR 太大或跨多個 issue 時提出來。
- **開 PR 前先把最新 `master` merge 進你的 branch**——CI 測的是*merge 後*,base 過舊會冒出意外失敗。本機用
  `uv sync --frozen` 對齊 CI 的 `ty` 環境。CI 期間(約 30 分鐘)master 常會再前進 → 重 merge 到 PR
  MERGEABLE/CLEAN。
- commit 訊息結尾加:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## 13. 禁區與地雷

- **`configs/config.yaml` 是禁區**——live、gitignore、含明文 secret。只讀/改 `configs/config.example.yaml`。
  key 有變動時,把替換片段交給 operator 自己改 `config.yaml`(loader 的 strict-unknown-key 會對舊 config 報錯);
  絕不把看到的 secret 貼進任何 commit 的檔案。
- **新增 config key 必須加進 loader 的 strict key-validator schema**,否則開機失敗。
- 不要重定義 specstar metadata、不要用 `uid`、不要 fetch-all + Python 過濾——見 §4.3。
- **`design_handoff_rca_3.0/` 不要改**(見 §4.6)。

---

## 14. 測試備註

- integration 測試(`@pytest.mark.integration`)在本機缺 daemon/工具時自動 skip;它們不在 CI 跑(見 §3)。
- Ollama live 測試在 daemon/模型不在時自動 skip;它是唯一會真的打模型的測試(`# pragma: no cover` 圈住 live 路徑)。
- user-namespace 隔離測試在 userns 不可用時 skip。
- FE 需要 DOM 的測試用 `// @vitest-environment happy-dom`。
- specstar 的 `.contains` 在 in-memory backend 永遠是成員判斷,**抓不到 Postgres-only 的 substring 退化**
  (見 §4.3)——這類查詢要額外用真 Postgres 驗。

---

## 15. Definition of Done

- [ ] 行為由 `/tdd` 驅動;目標測試綠 + `ruff check` + `ruff format --check` + 整專案 `ty check`。
- [ ] 收尾跑一次全套 + **100% 覆蓋率** gate(`coverage run … && combine && report --fail-under=100`),
      失敗有真的浮出來(沒被 pipe 遮住)。
- [ ] FE 改動:vitest 綠 + `pnpm run typecheck` + `pnpm run build`;SSE schema 兩邊同步。
- [ ] LLM 功能:對真的本地模型跑過 **live canned check**。
- [ ] 有文獻背書的選擇,引用放進程式 + PR 內文。
- [ ] 使用者可見字串去術語化 + 走 i18n;不露內部細節。
- [ ] 新 config key 寫進 `config.example.yaml` + 加進 loader schema。
- [ ] 用語對齊 [`CONTEXT.md`](https://github.com/HYChou0515/ai-workspace/blob/master/CONTEXT.md)。
