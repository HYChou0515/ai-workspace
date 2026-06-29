# 設計決策（Design Decisions）

這裡記的是**為什麼這樣設計、否決了什麼**——把散落在 [architecture.md](architecture.md) §9、`CLAUDE.md` 慣例清單、各 `plan-*.md` 與 issue 討論裡的「為什麼」整合成一張可查的決策表。**權威的「怎麼運作」說明在各子系統文件**（`subsystems/*.md`）；本頁只負責 rationale 與被否決的替代方案，方便回頭追問「當初為什麼不那樣做」。

> 出處欄盡量帶 `#issue`、`plan-*.md` 檔名或子系統連結。更深的 rationale 與被否決方案另見專案 `/grill-me`（Q1–Q12）對話紀錄。

---

## 架構與分層

| 決策 | 理由 | 否決的替代方案 | 出處 |
|---|---|---|---|
| 各層皆 Protocol（duck typing），靠 `create_app(...)` 注入 | 單點抽換：換掉任一層（sandbox / filestore / runner / embedder）不需動其他層；測試注 Mock/Scripted、正式注真實作 | 具體類別直接相依、繼承式框架 hook——換實作就要改呼叫端 | [architecture.md](architecture.md) §1, [subsystems/api-and-turns.md](subsystems/api-and-turns.md) |
| 新介面用 `abc.ABC`、命名 `I<Name>`（如 `IMonitor`），介面/實作分檔 | 明確的抽象邊界與型別檢查，比結構型 Protocol 易讀易導航；**不**回頭大改既有 Protocol | 全面把舊 Protocol 遷成 ABC（無謂的 churn） | `CLAUDE.md` 慣例；user memory `feedback_abc_over_protocol` |
| Sandbox 由 agent 的 `exec` 工具**首次使用**才延遲建立；純檔案操作走 FileStore，永不開 sandbox | 不跑 shell 的對話零成本（不必為了 read/write/ls 起容器） | 每回合預先建好 sandbox（grill-me 否決的「a1」策略） | grill-me Q10「a2+」；[architecture.md](architecture.md) §5, [subsystems/sandbox-and-filestore.md](subsystems/sandbox-and-filestore.md) |
| reverse-sync **不**因 sandbox 少檔就刪 FileStore 的檔 | 刪除反向傳播太危險，誤刪真相不可逆；清理交給 Files API | 完全鏡像（含刪除） | [architecture.md](architecture.md) §5 |
| FileStore「誠實目錄」（真的支援空目錄）| 用 `mkdir/rmdir/is_dir/listdir` 真支援空目錄 | 靠 `.keep` 之類 placeholder hack | [architecture.md](architecture.md) §5 |

## Agent 回合與串流

| 決策 | 理由 | 否決的替代方案 | 出處 |
|---|---|---|---|
| `AgentRunner` Protocol 是 scripted ↔ 真 LLM 的抽換點 | 測試（`ScriptedAgentRunner`）不依賴 LLM；SSE plumbing 可獨立開發；正式用 `LitellmAgentRunner` | 測試直接打真模型（慢、不確定、要外部依賴） | [architecture.md](architecture.md) §9, [subsystems/agent-runtime.md](subsystems/agent-runtime.md) |
| RCA workspace 與 KB chat 回合共用同一個 `ChatTurnEngine`（`api/turns.py`）| turn/cancel/SSE/序列化邏輯只一份；每個 conversation 一把 lock、一個可取消的 in-flight turn（新訊息取消前一個）；兩邊只注入各自的 `AgentToolContext` + `on_complete` | 每個 surface 各刻一套 turn/cancel/SSE | `CLAUDE.md`；[architecture.md](architecture.md) §3, [subsystems/api-and-turns.md](subsystems/api-and-turns.md) |
| `InvestigationRegistry` 只管 sandbox 生命週期（RCA 專屬），不管 turn | turn 已抽進共用引擎；registry 只剩它無可取代的職責 | registry 同時管 turn + sandbox（職責混雜） | [architecture.md](architecture.md) §3, [subsystems/api-and-turns.md](subsystems/api-and-turns.md) |
| SSE event schema 在 BE/FE 鏡像（`api/events.py` ↔ `web/src/events.ts`）| 同一份事件契約兩端共用；新增事件型別必須兩邊同步，否則 FE 渲染漏接 | 各自定義、靠文件對齊（易 drift） | [architecture.md](architecture.md) §4/§6, [subsystems/frontend.md](subsystems/frontend.md) |
| `_run_once` 把 producer（SDK 事件）與 `on_exec_output`（exec stdout）fan-in 進一個 queue | Agents SDK 工具是 request→response，執行期間無回報 stdout 的管道；fan-in 才能讓長指令輸出邊跑邊變成 `ToolLog` | 等工具整段跑完才顯示輸出（長指令像卡死）| [architecture.md](architecture.md) §3, [subsystems/agent-runtime.md](subsystems/agent-runtime.md) |
| 無綁定 `AgentConfig` 時 fallback 到 store **第一個**（最早建立）| 預設代理是真的 seeded config（如本機 Qwen3），不是空殼 `workspace-agent` | 退回一個 bare default 空殼 | `CLAUDE.md` `_resolve_agent_config`；[architecture.md](architecture.md) §9 |
| 每個 LLM 呼叫都**串流**；程式碼中無非串流 `chat.complete` | 串流才有即時 thinking／可觀測性；callers 累加 + forward chunk | 非串流一次拿整段（觀測性差） | user memory `feedback_always_stream_llm` |

## 模型選擇

| 決策 | 理由 | 否決的替代方案 | 出處 |
|---|---|---|---|
| 預設本機 Qwen3（Ollama）而非 hosted | 成本/隱私；模型是可抽換的外部依賴，用 LiteLLM 模型字串即可切換 | 預設 hosted OpenAI/Claude（綁定外部付費/資料外流）| [architecture.md](architecture.md) §9；user memory `feedback_llm_choice`, `feedback_ai_external_dependency` |
| 小模型失敗模式帶提示重試（`diagnose_error`）| Ollama chunk_parser 在多工具呼叫會吐無效 JSON，帶提示重試比硬失敗好 | 一次失敗就終止回合 | [architecture.md](architecture.md) §9, [subsystems/agent-runtime.md](subsystems/agent-runtime.md) |
| LLM 功能 DoD 要含 live canned check | fake-LLM 測試 ≠ 功能真的可用；replay 只跑 context→LLM、不跑工具 | 只靠 fake-LLM 單元測試就宣告完成 | user memory `feedback_llm_features_need_live_checks` |

## 知識庫（KB）

| 決策 | 理由 | 否決的替代方案 | 出處 |
|---|---|---|---|
| 攝取切成 `store`（快、同步）+ `index`（慢、背景），兩者皆 `asyncio.to_thread` offload | 慢的 magic 嗅探／specstar I/O／嵌入 HTTP 不壓 event loop；文件即時以 `indexing` 出現再翻 `ready`/`error`，上傳回應不被擋 | 同步在 request handler 內切塊+嵌入（阻塞 event loop、卡住所有請求）| `CLAUDE.md`；[architecture.md](architecture.md) §8/§9, [subsystems/kb-ingest-index.md](subsystems/kb-ingest-index.md) |
| KB agent 重用同一個 `AgentRunner` + `AgentToolContext` 雙形態 | 不另寫一套 agent loop；RCA 欄位改 optional，KB 只帶 `retriever` + `collection_ids` + `kb_search`（無 sandbox）| 為 KB 另寫獨立 agent runner（重複 turn/串流邏輯）| `CLAUDE.md`；[subsystems/kb-retrieval-agent.md](subsystems/kb-retrieval-agent.md)；user memory `project_kb_phase3_agentic` |
| `kb_search` 與 `ask_knowledge_base` **不可合併** | `kb_search` 是 KB agent 自己的檢索 leaf（需 retriever+collection_ids）；`ask_knowledge_base` 把整題委派給 kb_chat 子代理做 context 隔離（吵雜檢索留在子代理 throwaway context，省消費者 window）。若合一，KB 內層代理會對自己無限呼叫 `ask_knowledge_base`——leaf 不可化簡 | 一個工具同時當 leaf 與 consumer-interface（遞迴 + context 污染）| #270；`CLAUDE.md` |
| 應用代理一律 grant `ask_knowledge_base`，**不** grant `kb_search`/`search_wiki` | 後者需要該 app 沒有的 retriever/wiki context，會直接失敗；只有「本身就是 KB/wiki 代理」者才配 leaf 工具 | 給每個 app 直接 grant `kb_search`（缺 context 即崩）| #270；`CLAUDE.md` |
| `lookup_glossary` 例外，可直接 grant 任何 app | 它是便宜、確定性、exact-key 的 context-card 查表（無 LLM、無 retriever）| 把它也藏在子代理後面（無謂開銷）| `CLAUDE.md`；user memory `project_issue_106_context_cards` |
| `ask_knowledge_base` 把 KB 子代理進度 relay 進父流（`on_exec_output`/`ToolLog`）| 否則 RCA 卡著等整段答案、看不到 KB 在搜什麼 | 子代理 silent 跑完才回答（像停住）| [architecture.md](architecture.md) §8/§9 |
| 嵌入由我們算、原始向量存在 `DocChunk`（`Vector`, cosine）| 控制非對稱 query/doc prefix；dense 檢索可下推 specstar 原生向量查詢（pgvector 走索引）；`KB_EMBED_DIM` 變更需重新索引 | 交給外部向量 DB / 讓 specstar 算嵌入 | `CLAUDE.md`；[architecture.md](architecture.md) §9, [subsystems/kb-retrieval-agent.md](subsystems/kb-retrieval-agent.md) |
| `SourceDoc` id ＝ 自然鍵 `{collection_id}/{path}`（**path-keyed，非 per-user**）以 `/`→`∕`（U+2215）編碼成 slash-free 不透明 token，**永不解析** | specstar id 不能含 ASCII `/`，且 id 會出現在對使用者顯示的連結（`kb://doc/{id}`），故用 look-alike `∕` 而非百分比編碼（後者會弄醜 `:`／空白／unicode）；要 path/collection 一律讀記錄欄位 + `created_by` meta | 百分比編碼整個鍵（弄醜可讀 id）／把 id 當可解析結構化 key 拆字串（脆弱、洩漏內部格式）| `kb/doc_id.py` docstring；[subsystems/data-layer.md](subsystems/data-layer.md)（注意：`CLAUDE.md`／`CONTEXT.md` 仍寫舊的 per-user＋percent-encode，已過時） |
| 切塊是 hyperparameter：parser 吐整檔 `Document`，splitter 才決定粒度 | 關注點分離；要 grill 的是 text shape / prompts，不是 chunk size | parser 直接吐已切好的小塊（granularity 寫死在解析層）| user memory `feedback_chunking_hyperparams`；[plan-kb-parsers.md](plan-kb-parsers.md) |

## 背景任務與擴展

| 決策 | 理由 | 否決的替代方案 | 出處 |
|---|---|---|---|
| Job runner ⊥ API：coordinator 由 FastAPI-free 的 `coordinators.build_coordinators` 統一建構（#312）| 同一份組裝給 `create_app` 與獨立 `python -m workspace_app.worker <jobtype>` 共用；API 經 `server.run_consumers` gate 可變純 producer，各 JobType 獨立 pod 化掛自己的 k8s HPA | 把 coordinator inline 在 `create_app`（worker 無法共用、無法各自擴展）；用 KEDA | #312；`CLAUDE.md`；[subsystems/jobs-and-scaling.md](subsystems/jobs-and-scaling.md)；user memory `project_issue_312_job_runner_split` |
| 非佇列 sweeper（idle_killer/mirror/index/blob_gc/code_sync）永遠留在 API、不 gate | 它們是 per-pod sandbox 回收與本地維護，無共享 backend 概念 | 把 sweeper 也丟進 worker pod（語意不符）| #312；`CLAUDE.md` |
| 大 index/sanity job fan-out 成小 per-unit job + CAS join（#227）| RabbitMQ 對長時間 consumer-ack 會 406 timeout；切小單元 + CAS join 才不超時（`partition_key` 在 RabbitMQ 被忽略，需顯式 join）| 單一大 job 長跑（consumer-ack timeout）| #227；[plan-issue-227.md](plan-issue-227.md)；user memory `project_issue_227_index_fanout` |

## specstar 慣例

| 決策 | 理由 | 否決的替代方案 | 出處 |
|---|---|---|---|
| 永遠 `SpecStar()` 建新實例，不用 module-level `specstar.spec` singleton | 測試隔離靠這點 | 共用全域 singleton（測試互相污染）| `CLAUDE.md`；[architecture.md](architecture.md) §7 |
| 要 filter/sort 的欄位先 index（`add_model(indexed_fields=[...])`）+ 用 QB 查 | 推給 backend 做 filter/sort/aggregate；不要 fetch-all 再 Python 過濾 | 全表撈出來在 Python 過濾（不可擴展）| `CLAUDE.md`；user memory `reference_specstar_indexed_queries` |
| 頁面 aggregate 一律 `exp_aggregate_by(query=...)` scope 到該頁 ids/collection | 不為查一頁做全域 group-by；`.contains` 是 membership filter 不是 group-by | 全域 group-by 再挑一頁（浪費）| `CLAUDE.md`；user memory `project_issue_103_chunk_count_agg` |
| `.contains` 在 indexed `list[str]` 上是**精確 element membership**（非 substring）| specstar ≥0.11.9：Postgres `@>` / SQLite `json_each`，`"m4"` 不會誤中 `"m40"`；前提是欄位留在 `indexed_fields` 且註記 `list[...]`，否則 SQL 退回 substring `LIKE` | 假設 `.contains` 是子字串比對 / 不維持 list 註記（Postgres-only 回歸，in-memory 測試抓不到）| #378/#362、#181；`CLAUDE.md`；user memory `reference_specstar_indexed_queries` |
| 新索引回填舊 row 用 `Schema.step(None, ...)` + migrate 路由（`POST /{model}/migrate/execute`），不手刻 reindex loop | specstar 寫入時抽 `indexed_data`、不自動回填；`rm.migrate` 是唯一回填 op，`MigrateRouteTemplate` 全域 opt-in 掛載 | 手寫 reindex 迴圈重抽 indexed_data | #365/#366；`CLAUDE.md`；user memory `reference_specstar_migration` |
| specstar 自動 CRUD 路由可接受，model 即 resource、storage 可換 | 不包一層去藏它的路由；用 specstar metadata（created/updated/revision）不自己重定義 | 把 model 包成 blob 藏路由（`SpecstarFileStore`-as-blob 反模式）| user memory `feedback_specstar_routes_fine`, `reference_specstar_metadata` |
| specstar struct 欄位用 `dict[str, Any]`（非 `dict[str, object]`）| `object` 破壞 JSON-schema 生成；`resource.data` 用 `assert isinstance(...)` 收斂給 ty | 用 `dict[str, object]`（schema 生成壞掉）| `CLAUDE.md` |

## 流程與規範

| 決策 | 理由 | 否決的替代方案 | 出處 |
|---|---|---|---|
| Plan phase 用 **flat 整數序列**（Phase 1, 2, …），不用 1a/1b | 「Phase 1」＝該整數要被完成；切分出去就是下一個整數 | 字母後綴 sub-phase（Phase 1a/1b）| `CLAUDE.md`；user memory `feedback_phase_numbering` |
| 新功能/bug 先 `/grill-me` 壓測計畫，再 `/tdd` red-green-refactor 實作 | 寫碼前先解決開放問題；測試先行驅動 | 先寫實作再補測試 | `CLAUDE.md` |
| 用 coverage.py 直接跑、parallel + combine，**不**加 pytest-cov | 100% gate 在完整本地 suite；CI 只跑 `-m "not integration"` 不 gate 100% | 用 pytest-cov；用 `pytest | tail` 遮蔽失敗 | `CLAUDE.md`；user memory `feedback_python_toolchain`, `feedback_gate_no_pipe_mask` |
| 端點回傳 typed pydantic model，不回 bare dict | OpenAPI/驗證/FE 對齊；改到的端點順手轉並鏡像 FE 型別 | 直接回 `dict`（契約鬆散）| user memory `feedback_pydantic_response_models` |

---

> 找不到某決策的「怎麼運作」細節？先看對應的 `subsystems/<slug>.md`；本頁只負責「為什麼」。
