# 資料遷移（Migrations / 索引回填）

有些升版會改變「資料在資料庫裡的儲存形狀」，但 **specstar 只在寫入當下**把一列的
`indexed_data` 算好，之後**不會自動回填**。所以既有的資料列會停在舊形狀，直到每一列
被**重新寫過一次**為止。本文說明什麼時候需要做這件事、怎麼用內建腳本做、以及做完之後
怎麼把索引空間收回來。

> 一句話：**升版只讓「未來的寫入」變乾淨；既有的列要靠 migrate 重寫才會跟上。**

**趕時間的話直接看下面的 §5 案例總表** —— 目前有哪些 model 需要回填、沒跑會怎樣，都在
那一張表裡。其中 **`workspace-file`（§8）會讓 rollout 停住**，而且回填**不能打 Service**。

---

## 1. 什麼時候需要跑

當一次部署做了下面任一件事，既有列就會落後，需要一次 migrate：

- **新增了一個索引**到既有 model（例如替某欄位加上 `IndexableField`）。舊列在那個
  索引加入前就寫好了，不會出現在新索引裡，聚合時會少算。
- **改變了 `indexed_data` 的算法**。最典型的就是 **specstar 0.12.1**：`Vector` 欄位
  不再被複製進 `indexed_data`（它本來就有自己的 pgvector 欄位），但這只對**新寫入**
  生效；既有列的 `indexed_data` 還帶著整條 4096 維向量，被 GIN 逐元素索引 —— 那正是
  讓文件列表變慢的元凶（見 §6）。
- **改變了「衍生欄位」的算法**。有些欄位不是原始輸入，而是從原始欄位算出來的比對鍵
  （例如 graph 的 `norm_subject` / `norm_period`）。規則一改，既有列還帶著舊規則算出來的
  鍵。這一種要跑的不是 no-op 重抽，而是一個**真的會改資料**的 step（見 §2、§9）。

如果你這次部署沒動索引、沒動 `indexed_data` 的算法，也沒動任何衍生欄位的規則，就
**不需要**跑 migrate。

---

## 2. 為什麼「光升版」不夠 —— 兩層機制

migrate 是「把一列重跑一次目前的寫入路徑」，重跑時 `indexed_data` 會被**重新萃取**成
最新形狀。但它有一個保護：

> **migrate 會跳過任何「已經在最新 schema 版本」的列。**（specstar 在 route 層與
> `ResourceManager.migrate` 各有一道 gate。）

也就是說，如果一個 model 的既有列都已經在最新版本，`POST /{model}/migrate/execute`
會對每一列回報 `skipped`，什麼都不做。要讓 migrate 真的動手，必須先給那個 model 的
`Schema` **升一版**，用一個 **no-op 的 `_reindex_only` step**（資料不變，只是逼出「重新
萃取 + 重新寫回」這個副作用）——這是**程式碼變更**，在 `src/workspace_app/resources/__init__.py`
裡。針對 §6 的向量清理，這個程式碼變更**已經做好並隨版本發出**。

**step 有兩種形狀，別只記得第一種：**

| 形狀 | step 函式 | 資料 | 例子 |
| --- | --- | --- | --- |
| **重抽索引** | `_reindex_only`（identity） | 不變 | §6 §7 §8 —— 目的純粹是逼出「重新萃取 + 寫回」這個副作用 |
| **重算衍生欄位** | 真的回傳一個改過的 record | **會變** | §9 的 `graph-claim`：`_renormalize_claim` 依當前規則重算比對鍵 |

兩種都必須是**純函式**（只讀該列自己的欄位，不能載入別的 resource），寫法照抄既有的即可。
差別在於第二種**改得動資料**，所以 dry-run（§3）在它身上更值得跑一次。

> ⚠️ **不是每個 `Schema` 都住在 `resources/__init__.py`。** `workspace-file` 的在
> `src/workspace_app/filestore/specstar_impl.py`，各個 job model 的在自己的 coordinator
> 裡。要盤點「還有誰需要 migrate」，請對整個 `src/` grep `Schema(` —— 只看
> `resources/__init__.py` 會漏掉 §8，而 §8 正好是唯一一個會卡住部署的。

---

## 3. 怎麼跑 —— `scripts/run_migrate.py`

腳本會對每個 model 打它的 migrate route、串流進度、依狀態統計
（`skipped` / `success` / `failed`），最後印出回收空間要下的 SQL。

**先 dry-run**（走 `migrate/test`，串流一模一樣的進度但**不寫回**）：

```bash
uv run python scripts/run_migrate.py --dry-run doc-chunk cluster-member
```

確認沒有 `failed` 之後，**正式跑**（會重寫每一列的 meta，請挑低流量時段）：

```bash
uv run python scripts/run_migrate.py doc-chunk cluster-member
```

非預設主機、或有掛 `root_path`：

```bash
uv run python scripts/run_migrate.py --base-url https://kb.example.com doc-chunk
```

route 掛在 `/api` 底下（`POST /api/{model}/migrate/execute`），身分沿用部署設定的
`server.default_user`，所以不需要另外帶 token。任何一個 model 出現 `failed` 或連線
失敗，腳本會以 **exit code 1** 結束並列出是哪些列。

---

## 4. 收尾：`REINDEX` 回收空間

migrate 把每一列重寫成精簡的 `indexed_data` 之後，**查詢速度會立刻恢復**（GIN 不再需要
比對那條向量），但**索引檔本身的體積**要等 `REINDEX` 才會縮回來 —— 舊的索引項會留成
dead entry。腳本會在成功後把要下的指令印出來，形如：

```sql
REINDEX TABLE CONCURRENTLY doc_chunk_meta;
REINDEX TABLE CONCURRENTLY cluster_member_meta;
```

- 用 `REINDEX TABLE`（而非指名某個索引），因為它按 **table 名**運作、對 specstar 的
  索引命名細節免疫；一次把該 meta table 的所有索引都重建乾淨。
- meta table 的名字是 `<table_prefix><model 的 snake 形>_meta`。預設部署沒有前綴，
  所以 `doc-chunk` → `doc_chunk_meta`、`cluster-member` → `cluster_member_meta`。若你的
  部署有設 `table_prefix`，用 `--table-prefix` 讓腳本把它印進去。
- `CONCURRENTLY` 不鎖表，可以在服務運作中一個一個跑。

---

## 5. 案例總表

每一列都是一次「既有資料會落後」的升版。**這張表是 repo 的事實**（哪個版本、哪個 commit
帶進來的）；**某個環境跑過沒有，repo 看不到**，要各環境自己確認。

| model | 現行 Schema | 帶進來的 commit | 沒回填的話 | 細節 |
| --- | --- | --- | --- | --- |
| `workspace-file` | v3 | `01b42392`（2026-07-29） | ⚠️ **新 pod 永遠不 ready、rollout 停住** | §8 |
| `doc-chunk` | v6 | `38b4ab58`（2026-07-20） | 文件列表慢；舊 chunk 的關鍵字檢索找不到 | §6 §7 |
| `cluster-member` | v1 | `cfe021e8`（2026-07-16） | 文件列表慢 | §6 |
| `graph-claim` | v3 | `19aa23b3`（2026-07-24） | 比對鍵停在舊規則 | §9 |
| `graph-mention` | v2 | `c1616a0d`（2026-08-03） | 比對鍵停在舊規則；走訪也要逐列解 blob | §9 |
| `graph-entity` | v1 | `e21369fb`（2026-08-03） | 走訪要逐列解 blob，慢 | §9 |
| `graph-entity-link` | v1 | `e21369fb`（2026-08-03） | 走訪要逐列解 blob，慢 | §9 |
| `graph-relationship` | v1 | `e21369fb`（2026-08-03） | 走訪要逐列解 blob，慢 | §9 |
| `source-doc` | v10 | plan-rag-context P3（2026-09-12）；`path` 索引本身是 `d1004107`（#263，2026-06-27）加的但當時沒上帳 | ⚠️ **「限定資料夾」搜尋看不到舊文件**：資料夾範圍用 `path.starts_with` 解析，`path` 索引之前寫入的列答不了它，就被當成不在那個資料夾（是**少列**不是排錯）。P2 的前後文走訪是列整個 collection 再從 row 資料讀 `path`，**不**受影響 | §5（本列） |
| `deployed-wui` | — | WUI 總覽（2026-09-16，`docs/plan-wui-overview.md`） | **不用回填**：新表，上線時沒有任何列；一列只在有人按 Deploy 時寫入（`api/wui_deploy.py`），舊頁面要出現在總覽得再按一次 Deploy——這是設計，不是遺漏。**2026-09-18 加了 `icon` 欄位**（`plan-wui-overview-icon-favourites.md`）：同樣不用回填，欄位有預設值 `""`，在加欄位之前 Deploy 的列讀出來就是「沒有 icon」、總覽畫預設圓圈；要 icon 就在 view 檔加 `icon:` 再按一次 Deploy；同日再加 `color` 欄位，同樣預設 `""`、同樣不用回填 | — |
| `notification` | — | 待填（WUI 第三輪） | **想要的行為,不用回填**：舊通知不帶 `outbound` 索引值,所以外送掃描永遠看不到它們——第一次接上寄信通道時,不會把平台歷史上所有通知都寄出去一遍 | §5.6 |

一次盤點全部（**dry-run 不寫回，安全**，§3）：

```bash
uv run python scripts/run_migrate.py --dry-run \
  workspace-file doc-chunk cluster-member source-doc \
  graph-claim graph-mention graph-entity graph-entity-link graph-relationship
```

已經在最新版的 model 會回一整排 `skipped`；沒有任何 collection 開 `use_graph` 的部署，那五
張 graph 表根本是空的 —— 兩種都不會壞事（§10），所以整串一起 dry-run 是安全的盤點方式。

⚠️ **正式跑的時候 `workspace-file` 要單獨處理**：它對「請求打到哪一個 pod」有硬要求，跟著
上面那串一起打會**回報成功、實際上什麼都沒做**。見 §8。

---

## 5.5 設定選項帳本（新旋鈕）

上面 §5 管的是**資料**落後；這張表管的是**設定**：每次升版帶進了哪些新的 config 選項。
重點欄位是「**不設會怎樣**」——新選項不一定是純加法，有的旋鈕就算不設，升版本身就改了
預設行為（那通常是修 bug 的預期變化，但 operator 必須看得到）。細節一律指向
`docs/configuration.md`，這裡只記帳，不重複規則本身。

**慣例：之後每個新增 config 選項的 PR，都要在這張表加一列。**

| 選項 | 帶進來的 PR | 不設會怎樣 | 細節 |
| --- | --- | --- | --- |
| `agents.presets.<自訂 kb preset>.allowed_tools` | plan-rag-context P3/P4（2026-09-12） | **釘死 `allowed_tools` 的自訂 kb preset 要加 `kb_grep`、`read_page`、`read_lines`**：內建的 `kb-*` preset 已加，kb prompt（`kb/prompts/system.md`）無條件描述這三個工具，自訂 preset 少列的話模型會被告知能用卻呼叫不到——#537 修過的「授予了卻拒絕」同一類。`kb_search_max=0` 時三個會跟文件一起關（prompt 會說明） | configuration.md §7 KB 聊天換模型 |
| `kb.retrieval.rerank_context_chars` | plan-rag-context P6（2026-09-14） | **新增上限，預設 4000**：rerank 每個候選最多看到 4000 字元的前後文（以命中為中心）。沒設時 `context_chars` 會讓 rerank prompt 長 4×／27×；設 `null` 才是不封頂、`0` 只看命中 | configuration.md §9 `rerank_context_chars` |
| `kb.retrieval.context_chars` | plan-rag-context P2（2026-09-12） | ⚠️ **行為有變**：預設 `2000`——每個檢索命中前後各至少多帶 2000 字元的原文（整塊 chunk、可跨到文件樹上的鄰居檔案），rerank 的 prompt 隨之變長，agent 看到的段落變寬；引用 `[n]` 仍指命中處。設 `0` 關掉（唯一不同於舊版的是 P5 的持有者命名修正，見 configuration.md）；`null`／負數**拒絕載入**（P7：之前 `null` 會過 loader、worker 第一次檢索就炸）| configuration.md §9 `context_chars` |
| `failover.rate_limit_budget_s` | #759（2026-09-03） | ⚠️ **行為有變**：agent 鏈碰到 429 從「快速燒完重試然後 giving up」變成「在原端點等它聲明的窗口」，等待秒數每次 agent run 共用一池，預設上限 2 小時；畫面會出現「請求過於頻繁，N 秒後自動重試」。設 `0` 回到一律切換的舊行為 | configuration.md §11 |
| `agents.subagent_models` | #770（2026-09-03） | **完全不變**：`run_agent` 不長 `model` 參數，sub-agent 照舊跟 parent turn 同一顆模型（review 以逐位元比對驗證） | configuration.md §7 |
| `history.max_tokens_window_ratio` | #767（2026-09-04） | ⚠️ **行為有變**：窗口解析多了一段「問 proxy 自己的 `/model/info`」。原本前四段全滅、上限只能是 `unknown` 的部署（自架模型掛在 litellm proxy 後面、用任意別名，最典型），`unknown` 的意思是**歷史從不裁切、自動壓縮從不執行**；現在若 proxy 只答得出 `max_tokens`，會用它 ×0.8 推出一個**標記為估計**的上限，於是裁切與壓縮開始運作。推導值裝不下已知開銷時一律拒收、退回 `unknown`（也就是舊行為）。⚠️ 這一格**沒有「設 0 回到舊行為」**——載入時要求 `0 < ratio <= 1`，`0` 會被擋下；要完全不走推導，就明確設 `history.context_limit`，讓第一段直接答得出來 | `configs/config.example.yaml` 的 `history:` 區塊 |
| `server.max_page_schedules` | #788（2026-09-05） | **完全不變**：預設 1000,程式碼裡的預設值一樣。這是**失控護欄不是政策限制**——正常的頁面碰不到,碰到代表那個頁面有 bug。它擋的是耐久狀態:每個排程觸發過就在視窗帳本留一列,而沒有別的東西限制頁面能建幾個 | configuration.md |
| `server.notification_channel` | #788（2026-09-05） | **完全不變**：通知只寫站內信,跟這個接縫出現之前一模一樣。沒有背景外送迴圈會被啟動(空值連 timer 都不建),也沒有任何查詢會多跑 | configuration.md |
| `server.workflow_step_timeout_sec` | #788（2026-09-05） | ⚠️ **行為有變**:工作流裡單一 agent 步驟從「沒有上限」變成 **10 分鐘就中止那一步**,訊息裡會寫出那個秒數。機制本來就在(`steps.py` 的 `asyncio.wait_for`),但**沒有任何地方把值傳進 `create_app`**,所以每個部署實際上都跑在無上限那條分支——這次補的是接線。有合法長於十分鐘的 agent 步驟的部署要調大,或設 `0` 回到舊行為 | configuration.md |
| `server.trigger_check_interval_sec` | #788（2026-09-05,既有選項,語意擴大） | ⚠️ **不設就完全沒有排程**,而且沒有錯誤訊息。這一顆本來只管工程師寫的 `triggers.json`,現在同時決定頁面自己寫的 `schedules.json` 會不會被掃到——預設仍是 `0`(不掃),所以「頁面排程」這個功能在沒有開這顆的部署上**存在但永遠不動**。刻意共用一顆而不是新增第二顆:兩顆管同一件事保證有一天會不一致。要用就設個秒數(60 是合理起點),那個數字就是「最晚會遲到多久」 | configuration.md |
| `sandbox.uv_cache_max_bytes` / `SANDBOX_HOST_UV_CACHE_MAX_BYTES` | #775（2026-09-04） | ⚠️ **行為有變,但方向是省事的**:profile 帶 `pyproject.toml` 時,uv 的下載快取現在**活得比 sandbox 久**(每個 item 一份,放在 sandbox 目錄旁邊),所以同一個 item 的下一次冷啟動不必重抓。**不設就沒有上限、不會淘汰**——和這裡每個其他限制的「unset = 無限」一致。因此不設的風險是**磁碟只增不減**:host 那份在 pod 的 ephemeral 磁碟(沒有宣告 ephemeral-storage 上限,塞爆會被 kubelet 驅逐),app 那份在共用 scratch 磁碟區(連 rollout 都不會清)。設一個數字,idle tick 就會依「最近最少使用」淘汰,而**活著的 sandbox 正在用的那份永遠不動** | `configs/config.example.yaml`(`sandbox.uv_cache_max_bytes`)與 `deploy/sandbox-host.example.yaml`(`SANDBOX_HOST_UV_CACHE_MAX_BYTES`);⚠️ app 端那個只在 **`kind: local` 且沒套 userns jail** 時有作用(`isolate: false`,或 `isolation.enabled: true`);`kind: http`(快取在 host 上)、`kind: docker`、`kind: mock`、以及**套了 jail 的 `kind: local`**(在支援 userns 但沒有 CAP_SETUID 的機器上就是 `isolate: null` 的 auto 預設)都沒有作用,四種都會在啟動時 warn —— 而且那個判斷是**建完之後問 backend 一次**,不是列舉入口點,所以之後新增的 backend 不會再漏掉 |
| `kb.cluster.sweep_interval_seconds` / `kb.cluster.merge_tau`（既有選項,語意改變） | #804（2026-09-14） | ⚠️ **行為有變,而且是這次 OOM 的修法**:待審核 inbox 的 cluster sweep(backfill 未投影候選 + 折疊 race-split 分群)**不再在 API pod 上執行**。以前每顆 API pod 各自每 900 秒把每個 collection 的全部 `ClusterMember` 讀進記憶體、再逐候選打 embedding——零流量也照跑、N 顆 pod 跑 N 遍,這就是 API heap 一路長到 OOM 的元兇。現在 API 每 `sweep_interval_seconds` 只 **enqueue** 一個 `cluster_sweep` 的 card-gen job(fleet-wide 一個 window 只有一顆 pod 問——同一份 window ledger 上的 `__scan__:cluster-sweep` lease;同 collection 已有 queued/running 的再 coalesce;每次 sweep 開始前先硬刪同 collection 其他終態（COMPLETED / FAILED）的 sweep 列,所以無論成功失敗,job row 都不隨 uptime 長),真正的工作在 **card-gen worker** 上跑(`Reconciler.sweep`,跟 finalize-time reconcile 同一個 embedder、同一組 τ)。`merge_tau` 因此改由 `build_coordinators` 接到 reconciler——API 與 worker **兩個入口**都接了,有測試從 worker 的真入口 `build_bundle` 釘住。**不設什麼都不用改**;但 **`server.run_consumers: false` 而沒有跑 `rca-worker-card-gen` 的部署**,sweep job 會永遠 pending(以前是 API 默默做掉)——那種部署本來 card-gen 本身就不會動,所以不是新的洞,只是多一種 job 排在同一條隊上 | **怎麼起 worker:deployment.md §11「Job runner ⊥ API」**;`kubernetes/base/workers.yaml`(`rca-worker-card-gen`) |
| `server.trigger_check_interval_sec`（既有選項,語意再擴大） | #804（2026-09-14） | **單 pod 完全不變**。多 pod 時它現在也是**掃描的 window**：每顆 pod 以前每 tick 都各自重讀所有 profile 的 `triggers.json` 與每一頁的 `schedules.json`、逐 trigger 查 ledger,只為了讓「其中一顆」贏每個 trigger 的 window claim——claim 只去重觸發,不去重讀。現在同一份 ledger 上多兩列 `__scan__:triggers` / `__scan__:user-schedules`,每個 window 只有第一顆 tick 的 pod 真的掃,其餘那一 tick 什麼都不做。贏家半路死掉只損失那個 window（沒人重掃）,下個 window 重新選——「最晚遲到一個 interval」的承諾不變。每個 schedule / trigger 自己的 window claim **照舊**,跨重啟的冪等靠它。lease 存的是 window 起點的 epoch 毫秒,所以**事後調大這個值是安全的**（最多遲一個新 interval）。lease 列是 draft 原地改寫,永遠只有一個 revision;specstar 的 disk / Postgres store 每次改寫會留下前一個 uid 的孤兒 payload——那是上游 store 的性質,每個 draft 寫入者（含 job heartbeat,每 5 秒一次）都一樣,不是這個 PR 帶進來的,另開 specstar issue | configuration.md |
| （無選項）開機 `seed help collection` | #804（2026-09-14） | ⚠️ **行為有變**：Platform Help 的文件開機時只 **store**,變動的文件交給 index queue（一份文件一個 index job,記在 `system` 名下）,不再在 API pod 上 inline chunk + embed。內容沒變（絕大多數開機）照舊是 no-op。**`server.run_consumers: false` 而沒跑 `rca-worker-index` 的部署**,help 文件會停在 `indexing`、`/help` 的 KB chat 搜不到它們（以前 API 自己做掉）——那種部署本來所有 KB 索引都不會動,同一個洞,不是新的 | **怎麼起 worker:deployment.md §11「Job runner ⊥ API」**;`docs/subsystems/boot-and-config.md` |
| （無新選項）blob GC 改成 job：`blob-gc` JobType + `rca-worker-blob-gc` | plan-blob-gc-job（2026-09-17） | ⚠️ **行為有變,而且是 API pod OOM 的修法**：孤兒 blob 的 reconcile（specstar `gc(mode="reconcile")`）**不再在 API pod 上執行**。以前搶到 CAS lease 的那顆 API pod 每 `gc_interval_sec` 把每一個 blob-capable model 的**全部 ResourceMeta(含 `indexed_data`;doc-chunk / cluster-member 每一列都帶向量,§6 的 migrate 只瘦 JSONB 欄、不減這個數字)一次載進記憶體**、再逐筆串流 revision 算 live 集合——pod 死前最後一行就是 `blob-gc: won lease`。現在 API 每個 window 只 **enqueue** 一個 `BlobGcJob`（同一份 window ledger 上的 `__scan__:blob-gc` lease,fleet-wide 一個 window 一顆 pod 問;已有 queued/running 的 coalesce;每趟開始前先硬刪其他終態列,row 不隨 uptime 長）,真正的工作在 **`rca-worker-blob-gc`** 上跑。**這個 worker 開機組的是 API 自己那整套**（`__main__.build_app`,只組不 serve）——reconcile 只從已註冊的 model 算 live 集合,任何有 `list` / `dict` / union / `Optional` 欄位的 model 都會被掃（幾乎每一個）,拿部分註冊表跑會把缺的 model 引用的 blob 隔離、`gc_t2` 後刪掉;所以 registry 由建構保證相同,而且每次 enqueue 都帶 API 端的 model 清單,runner 少任何一個就拒跑（job FAILED、log 點名）。**不設什麼都不用改**;`server.run_consumers: false` 而沒有跑 `rca-worker-blob-gc` 的部署,孤兒 blob 就不再回收（只長不消,不掉資料;以前是 API 做掉,也是它 OOM 的原因），而且 `/monitor/summary` 的 `blob_gc` / `ws_census` 趨勢也會停（兩者都改由跑 pass 的那個 process 記）。**同一個 PR 順手修的既有 bug**：刪 item 的 cascade 對「從沒宣告 schedule 的 item」只容忍 model 未註冊、不容忍列不存在，lifespan 有跑的部署（每個真實部署）刪這種 item 都 500「failed partway」（2026-09-09 的 48f09a55 起）；現在 `_purge_schedule_index` 和 satellite purge 畫同一條線。另外 `_SandboxAddress` / `_ScheduleIndex` / `_TriggerWindow` / `_GoalStretch` 四個協調 model 改在 `create_app` 無條件註冊（以前在 lifespan、兩個看功能開關）——多兩個永遠空的 model（Postgres 上各三張表,`add_model` 時就建),無害。**跑完的 pass 會回收 job log**：specstar 把每個 job 的 log 存成 keyed blob（`__job_log__/<model>:<id>`），沒有任何 `Binary` 欄位引用它，而 reconcile 把 keyed blob 也當孤兒（`gc` 的 docstring 說不追蹤，`iter_active()` 卻列出來——上游要解的矛盾）；所以 pass 跑完後超過 `gc_t1` 的 job log 被隔離、`gc_t2` 後刪掉（約 25h），`GET /api/<job-model>/<id>/logs` 對更舊的 job 回 204。master 上 pass 在 prod 從沒跑完（OOM），log 活著是意外；這個 PR 讓 pass 跑完。舊的 `_GcLease` model 與它那一列 `blob-gc` 不再被讀,留著無害;程式碼已刪 | **怎麼起 worker:deployment.md §11「Job runner ⊥ API」**;`kubernetes/base/workers.yaml`(`rca-worker-blob-gc`,記憶體要照「一次持有單一 model 全部 ResourceMeta」給) |
| （無選項）DSL `workflow.json` 的 `agent` / `sandbox` step **`cache` 必填** | plan-cache-required（2026-09-16） | ⚠️ **破壞性**：既有省略 `cache` 的 `.workflows/*.json`（使用者 workspace 裡的，repo 改不到）部署後**解析失敗**——不再靜默消失：Workflows 面板列出它和原因（`steps[N]: \`cache\` is required — …`，沒有 Run）、排程列標「這個工作流程解析失敗」且不開火、`save_schedules` 拒絕。修法是由 AI/人用 `save_workflow` 補上每步的 `cache`（`false` 給會碰外界／有副作用的，`true` 給輸入全在 arguments 的）。起因：排程跑的 workflow 每步預設 `true`，第二次開火每步拿收據、什麼都不做。repo 內的範本與 playground `dsl` profile 已補齊 | workflows.md §22.2、§22.11；`author-workflow` skill |
| （無新選項）item 層級排程 `.workflows/schedules.json` + `save_schedules` 工具 | plan-item-schedules（2026-09-14） | **沒有新旋鈕**,但兩顆既有旋鈕的作用面變大:`server.trigger_check_interval_sec` 現在也決定 **AI 用 `save_schedules` 設的排程**會不會跑(預設 `0` 仍是不掃——差別是現在工具回覆和 Workflows 面板都會**大聲說**「這個部署沒開排程」,不再靜默);`server.max_page_schedules` 同一個上限也套在 item 層級那份檔案,而且在 AI 存檔時就擋(不是等到 sweep)。⚠️ 另一個行為改變不在 config:頁面 `startRun` 與排程的 `run` 從「只認 profile 宣告的 workflow」變成「profile ∪ item 自己 `.workflows/` 的」(和 Workflows 面板的 Run 對齊);沒有任何設定能回到舊判準——舊判準沒有正確的使用情境 | workflows.md §22.11 |

---

## 5.6 案例：`notification.outbound`——**故意不回填**

WUI 第三輪替 `Notification` 加了 `outbound`(`""` 待送 / `sent` / `無法送達`)並把它加進
`indexed_fields`,外送掃描就是查 `outbound == ""`。

specstar 不會自動回填,所以**這個欄位出現之前寫下的每一列都對不上任何值**,掃描永遠看不到
它們。

**這正是要的行為,不要去回填它。** 一個部署第一次接上 email 通道的那一刻,如果舊資料也被
掃到,平台歷史上每一則通知都會在那一分鐘內被寄出去一次。

⚠️ 所以這一列存在的目的跟其他列相反:**它是提醒你「不要跑」**,而不是提醒你「記得跑」。
如果哪天真的需要補送某一段期間的通知,那要是一次有明確時間範圍的一次性動作,不是
`migrate/execute`。

---

## 6. 案例：specstar 0.12.1 向量清理

0.12.1 讓 `Vector` 欄位不再進 `indexed_data`。**受影響的是兩個帶向量的 model**：

| model | 向量欄位 | schema 動作 |
| --- | --- | --- |
| `doc-chunk` | `embedding` / `embedding_alt` / `embedding_img` | v4 → **v5** 加一個 `_reindex_only` step |
| `cluster-member` | `embedding` | 原本**完全沒有 `Schema`**（migrate 會直接報錯），本次補上 `Schema` + `None → v1` step |

完整流程：

```bash
# 1. 部署帶有 0.12.1 + 上述 schema bump 的版本
# 2. dry-run 確認
uv run python scripts/run_migrate.py --dry-run doc-chunk cluster-member
# 3. 正式重寫
uv run python scripts/run_migrate.py doc-chunk cluster-member
# 4. 回收空間（腳本會印出這兩行）
#    psql:  REINDEX TABLE CONCURRENTLY doc_chunk_meta;
#           REINDEX TABLE CONCURRENTLY cluster_member_meta;
```

**範圍請留意**：這次清的是 **`indexed_data` 這個 JSONB 欄位與它的 GIN** —— 這是讓查詢
變快的關鍵。每一列的完整 meta（含向量）在另一個 `data` BYTEA 欄位裡還有一份，這次
**刻意不動**（當初拍板的「第一層」決定）。所以這修好的是**查詢速度**，不是完整的磁碟
體積；要連 BYTEA 一起收是另一個更大的決定（會改變讀取端反序列化到的內容），不在此列。

---

## 7. 案例：`doc-chunk` 的 `text` 索引回填（關鍵字檢索）

檢索改成**不再整包載入整個 collection** 之後，關鍵字（BM25）那半段改用 `DocChunk.text`
上的 **pg_trgm 索引**先把候選集縮小。而索引要看得到一列，`text` 必須先被萃取進那一列的
`indexed_data` —— 萃取**只發生在寫入當下**（就是 §2 那條規則）。

**所以升版後、回填前，既有的 chunk 對關鍵字檢索是隱形的：**

| | 回填前 | 回填後 |
| --- | --- | --- |
| 既有 chunk 的**關鍵字**檢索 | ❌ 找不到 | ✅ 正常 |
| 既有 chunk 的**語意（向量）**檢索 | ✅ 不受影響 | ✅ 正常 |
| **新上傳**的檔案 | ✅ 立即正常 | ✅ 正常 |

語意檢索照常運作，所以症狀不是「整個搜不到」，而是**舊文件的關鍵字命中率掉下去**——
這種半殘狀態不會噴錯，只會安靜地少給答案，所以請把回填當成部署的一部分，不要拖。

schema 動作：`doc-chunk` v5 → **v6**，一樣是一個 `_reindex_only` step（資料不變，只是逼出
「重新萃取 + 寫回」的副作用）。指令與 §3 相同：

```bash
# 1. 部署帶有 v6 的版本
# 2. 先 dry-run，確認沒有 failed
uv run python scripts/run_migrate.py --dry-run doc-chunk
# 3. 正式重寫（會重寫每一列的 meta，挑低流量時段）
uv run python scripts/run_migrate.py doc-chunk
```

**不需要手動建索引**：pg_trgm 擴充與那個 GIN 都由 specstar 在**每次開機**時確保存在
（`CREATE EXTENSION IF NOT EXISTS pg_trgm` + 建索引），只要 DB role 有權限即可。

**代價**：`text` 進了 `indexed_data`，等於每個 chunk 的文字在 JSONB 裡**多存一份**。這是
「用索引換掉整包載入」刻意付的成本，不是意外。大量重寫之後可以照 §4 跑一次
`REINDEX TABLE CONCURRENTLY doc_chunk_meta;`——這裡不是為了回收空間（`indexed_data` 是
變大的），而是讓重寫後的索引更緊實。

---

## 8. 案例：`workspace-file` 的 `path` 索引回填 —— 這一個會**卡住 rollout**

檔案樹和各種 entity 列表都走 `ls(prefix=…)`，而它把查詢**下推**到 `WorkspaceFile.path`
這個索引上（不下推的話，一個 3000 檔的 workspace 光列一次就要 796ms，而一次互動會呼叫
大約十次）。索引要看得見一列，`path` 必須先進那一列的 `indexed_data` —— 又是 §2 那條規則。

**這一個的後果和前兩個案例不同。** §6 是變慢、§7 是關鍵字命中率下降，都還能服務；`path`
沒回填的話，`ls(prefix=…)` 對舊列**一列都不回**，使用者看到的是**空的檔案樹、空的 entity
列表**，而資料完好無損。三個 replica 滾動更新時新舊 pod 答案不一樣，畫面會**閃爍** ——
讀起來就是資料遺失。

所以這一個帶了一道閘門：**`/api/readyz` 在回填完成前一律回 503**（`prefix_index_ready()`，
在 `src/workspace_app/api/app.py`），而 k8s 的 readinessProbe 就指著它
（`kubernetes/base/deployment.yaml`）。實務上的意思是：

> **忘記跑，你不會看到「慢」或「怪怪的」——你會看到新 pod 永遠不 ready、rollout 停在那裡。**

這是刻意的：寧可卡住，也不要讓任何一個 pod 端出「看起來是空的」workspace。liveness 故意
**沒有**指著同一個檢查，否則等待回填的 pod 會變成 crashloop —— 而 operator 正需要那些 pod
來跑回填。

### ⚠️ 回填必須打在**新 pod** 上，不能打 Service

這是這一節最容易做錯、而且**做錯了看起來像成功**的一步。

Service 只把流量導給 **ready** 的 pod，而卡住的時候 ready 的全都是**舊 pod**。舊 pod 的程式
裡 `WorkspaceFile` 的最新版是 **v2**（`indexed_fields` 沒有 `path`），而 migrate 只會把每一列
帶到「**該 pod 認得的**最新版」。所以打在 Service 上會**回報一整排成功、卻沒有把 `path` 抽
出來**，新 pod 依然永遠不 ready。

新 pod 雖然不 ready，但它**還活著**（liveness 走 `/openapi.json`），所以繞過 Service 直接連它：

```bash
# 1. 找一個新的、還沒 ready 的 pod
kubectl get pods -l app=rca-app

# 2. 直接連那個 pod —— port-forward 不經過 Service，不 ready 也連得到
kubectl port-forward pod/<新 pod 名稱> 8000:8000
```

### 部署順序

1. **先 rollout**。新 pod 起來但不會 ready —— 這是預期的；舊 pod 繼續服務，**沒有中斷**。
2. **port-forward 到一個新 pod**（上一小節），然後對著它跑：

   ```bash
   uv run python scripts/run_migrate.py --dry-run --base-url http://localhost:8000 workspace-file
   uv run python scripts/run_migrate.py           --base-url http://localhost:8000 workspace-file
   ```

3. **新 pod 自己會變 ready**，rollout 自然走完。不需要重啟任何東西。

要確認到底生效了沒，直接問那個 pod：

```bash
curl -i http://localhost:8000/api/readyz
# 503 + "workspace-file path index not backfilled" → 還沒好
# 200 + "ok"                                       → 好了
```

**全新安裝不受影響**：沒有舊列的時候，「`path` 以 `/` 開頭的列數」等於總列數，`readyz`
一開始就是綠的。

`path` 進 `indexed_data` 會讓索引變大（和 §7 同一種刻意付的代價）。大量重寫之後可以照 §4
跑 `REINDEX TABLE CONCURRENTLY workspace_file_meta;` —— 目的是讓索引緊實，不是回收空間。

---

## 9. 案例：graph 的五個 model（#534）

knowledge graph 的五個 model 各有一次升版，都需要一次回填：

| model | Schema | step 的形狀 | 沒回填的話 |
| --- | --- | --- | --- |
| `graph-claim` | v2 → **v3** | **重算衍生欄位**（`_renormalize_claim`） | 比對鍵停在舊規則 |
| `graph-mention` | → **v2** | **兩種都有**：`None → v1` 重算衍生欄位、`v1 → v2` 重抽索引 | 比對鍵停在舊規則；走訪也要逐列解 blob |
| `graph-entity` | 無 → **v1** | 重抽索引 | 走訪要逐列解 blob，慢 |
| `graph-entity-link` | 無 → **v1** | 重抽索引 | 走訪要逐列解 blob，慢 |
| `graph-relationship` | 無 → **v1** | 重抽索引 | 走訪要逐列解 blob，慢 |

```bash
uv run python scripts/run_migrate.py --dry-run \
  graph-claim graph-mention graph-entity graph-entity-link graph-relationship
uv run python scripts/run_migrate.py \
  graph-claim graph-mention graph-entity graph-entity-link graph-relationship
```

**嚴重度和 §8 差一級，不要混為一談。** `graph-entity` / `graph-entity-link` /
`graph-relationship` 這三個純粹是重抽索引，而且讀取端對「還沒回填」的列會**退回去讀
blob**，不會把缺席的欄位讀成空值 —— 所以這三個的回填是**加速**（走訪整個語料時只掃
metadata、不必逐列反序列化），**不是正確性閘門**。

**`graph-claim` 和 `graph-mention` 這兩個不一樣**，它們帶的是會改資料的 step：
`_renormalize_claim` / `_renormalize_mention` 依**當前**規則重算比對鍵
（claim 是 `norm_subject` / `norm_attribute` / `norm_period` / `norm_unit`，
mention 是 `norm_surface` / `norm_kind`）。沒回填的列還帶著舊規則算出來的鍵，依現行規則
本該視為同一件事的兩列，可能還是兩件。每一列都記著產生它的 schema 版本，所以「哪些還停在
舊規則上」是查得出來的，不是猜的。


**範圍**：graph 是 per-collection opt-in（`Collection.use_graph`，預設 `False`）。沒有任何
collection 開過的部署，這五張表是空的，跑起來不會有任何列 —— 跑一次當作確認即可。

---

## 10. 注意事項

- **挑低流量時段**：migrate 會重寫每一列的 meta。
- **順序**：先 dry-run，再正式跑，最後 `REINDEX`。
- **可重複執行**：重跑一個已經在最新版的 model 只會得到一整排 `skipped`，不會壞事。
  「重抽索引」型的 step 是 identity，所以這件事是白送的；**「重算衍生欄位」型不是
  identity**（§9 的 `graph-claim`），它可以重跑是因為每次都從該列的原始欄位重算 ——
  新增這種 step 的時候要**自己確認**這一點，別當成理所當然。
- **盤點要對整個 `src/` grep `Schema(`**，不是只看 `resources/__init__.py`（§2 的警告）。
- **未來要新增一次清理**：幫該 model 的 `Schema` 加一個 step 升版，然後把 model 名字丟給
  這支腳本即可 —— 機制是通用的，這支腳本不綁定任何特定 model。**順手把它加進 §5 的
  總表**，否則下一個人不會知道要跑它 —— §8 和 §9 就是這樣漏掉的：文件停在 2026-07-20，
  而那之後又有兩批升版沒人補上來。

---

## 不是資料遷移,但升版時值得看一眼:上下文窗口

本文其餘部分都在講 specstar 的索引回填。有一件**不需要跑 migrate、但需要你確認**的事
容易在升版後被誤會成故障:

**自動壓縮只有在解得出端點的 context 窗口時才會執行。** 解不出來時它完全不動,而畫面上
和「壓縮壞了」長得一樣。怎麼分辨、以及什麼時候才需要自己設 `history.context_limit`,
寫在 [部署說明 §11 —— 上下文窗口與自動壓縮](deployment.md#上下文窗口與自動壓縮誰決定怎麼確認什麼時候才需要你出手)。

規則本身只寫在那一邊 —— 兩份會漂移。

## 不是資料遷移,升版後也**不用**跑:legacy 切段器的中文修正(plan-rag-context P1)

`kb/chunker.py` 的 `FixedTokenChunker` 從「以空白隔開的一串算一個 token」改成「每個 CJK 字算一個
token」(字元類共用 `kb/tokens.py:CJK_RANGES`)。舊規則下中文沒有空白,一整段就是一個 token,所以
`max_tokens=256` 對中文等於 256 **段**:用這個切段器實測,一份 12,358 字的中文文件只切出 **1 個 chunk**。

**但 production 不走這個切段器。** API 與 worker 都接 `kb_pipeline=get_doc_pipeline(...)`(LlamaIndex
管線;`factories.py` 自己註明 legacy chunker 留給 tests + offline runs)。管線裡 PDF 文字層與純文字走
`SentenceSplitter(256/32)`(tiktoken 為底),用**真入口**實測:中文散文 12,358 字 → **80 塊、平均 154 字**;
中文 PDF 樣式 → 102 塊、平均 153;英文 50,038 字元 → 40 塊、平均 1,452。production 的中文切段本來就
正常(甚至比英文細十倍),**這個修正對 production 的 chunk 零影響,不需要重新索引。**

受影響的只有沒接 `kb_pipeline` 的 `create_app` 呼叫(測試、離線模式);那些環境的中文文件要重讀一次
(`POST /api/kb/collections/<collection_id>/reindex`,#390 的 index cache 會先被丟掉)。

## 不是資料遷移,但升版後要跑一次:整個 collection 重讀(plan-rag-context P6 + P8 + P9)

三個 phase 都改了 chunk 的**衍生資料**,都要靠重新索引才會落到既有文件上。找不出哪些文件受影響的話,
**每個 collection 重讀一次**(`POST /api/kb/collections/<collection_id>/reindex`;#390 的 index cache 會先被丟掉,
不會複製回舊資料)。

### P8:chunk 的 `start`/`end` 在多頁/多列文件和重複文字上是錯的

管線路徑的 chunk 位移一直是「相對於它那一頁/那一列的 Document」,不是相對於整份 `SourceDoc.text`(PDF 每頁、
PPTX 每張、CSV/XLSX 每列、JSONL 每行各是一個 Document,用 `\n\n` 接起來才是 canonical text);另外 LlamaIndex 用
`text.find(piece)`(**第一次出現**)定位切片,重複段落多的文件裡後面的 chunk 全指回前面。master 上沒人拿位移去切原文,
所以看不出來;這條分支的前後文、`kb_grep`、`read_page` 文字層都靠它,第 2 頁起全錯。P8 起新索引的位移是正確的位置;
**既有的多頁/多列文件與重複文字的文件,在重讀前位移仍是舊的**——症狀是 `read_page(N≥2)` 顯示第 1 頁的文字、`kb_grep`
找不到第 2 頁起的字、引用卡的前後文接錯段。受影響的是「有第 2 頁/第 2 列」的所有文件,實務上就是整個 collection。

`DocChunk` 多了一個欄位 `unit_start: int | None`(#227 fan-out 用,其他路徑與舊列都是 `None`):msgspec 預設值,
**不需要 migrate**。fan-out 的 chunk row 改為「不存在才建立」(P17):重複投遞的 batch 不再覆寫任何列。

### P9:P6 切出來的視窗沒有 page/section

P6 把長 Markdown 段落(含每一頁的 VLM 描述)切成多塊時,新的塊**沒帶** section 的 metadata,所以 `DocChunk.provenance`
是空的:`read_page(N)` 找不到那頁的文字層、`kb_grep` 不顯示 `(p.N)`、引用卡沒頁碼、#254 的 section 前綴也沒折進去。
P9 起視窗/表格/表格列都帶著 section 的 metadata;**P6 部署後、P9 部署前索引過的文件**要重讀才會補回頁碼。

### P6:超長 Markdown 段落

在真入口量到的 production 缺陷:管線對 **Markdown / VLM 輸出**走 `MarkdownNodeParser`,只按標題切、
**沒有大小上限**——沒有標題的 `.md`(或任何一個超長的段落、VLM 對一頁的描述)**不論多長都是 1 塊、1 個向量**
(實測 50,038 字元英文 → 1 塊;中文 12,358 字 → 1 塊)。跨語言,跟中文無關。

P6 起,超過 sentence 窗口(256 token)的 Markdown 段落會再被 `SentenceSplitter` 切成多塊(每塊 span 仍是原文的逐字切片、
帶標題 breadcrumb);**能放進一塊的段落逐位元不變**(#390 index cache 的 key 不受影響)。真入口實測:同一份無標題
英文 `.md` 從 1 塊 → 35 塊(平均 1,630 字元)、中文 → 80 塊(平均 154 字)。

升版只影響之後索引的文件,既有的超長段落仍是一塊,直到重新索引。要修的是**含長 Markdown 段落的文件**——典型是
無標題的 `.md`、匯出的筆記、以及所有靠 VLM 描述的圖片 / 掃描頁。

怎麼判斷還沒跑:文件頁上一份幾千字、沒有標題的 `.md` chunk 數是 1,就是舊切法。
