# 升級手冊（Migrations）

這份文件回答一個問題：**我要把線上從 master 的某個 commit 升到最新，除了部署新映像之外，
還要改什麼設定、跑什麼指令、k8s 那側要加什麼。** 它按 master 的合併順序排、由舊到新，
每一條標 `日期 · merge commit · PR`。

## 怎麼用

1. **找到你線上現在的 commit**（日期 + hash），從那一條**往下**做到底。閱讀順序就是執行順序。
   要精確知道中間合了哪些 PR，一行就夠（PR 編號就是條目的鍵）：
   ```bash
   git log --first-parent --oneline <你線上的 commit>..origin/master | grep -o '#[0-9]*'
   ```
2. **沒列的 PR 不用做事。** 這裡只列「運營方得動手」或「不動設定，行為或成本就會變」的 PR：
   資料回填、拿掉／搬家的設定 key（loader 對未知 key 嚴格，設了就**拒絕開機**）、預設值改變
   或預設就開的新行為、k8s／CI 那側要自己加的東西。純粹 opt-in 的新旋鈕不列，那是
   [configuration.md](configuration.md) 的事。
3. **同一個 model 的回填在好幾條裡出現時，最後跑一次就夠。** migrate 把每一列帶到「這顆 pod
   認得的最新版」，已在最新版的列回 `skipped`，重跑無害（機制見附錄 A）。唯一例外是
   `workspace-file`：它**一定要打新 pod**，見 [#668](#pr-668)。
4. **指令一律寫成現在的跑法**（`scripts/run_migrate.py`），舊條目也是——你升級到的是現在的程式碼。
5. 每個動作標**何時**：`rollout 前` / `rollout 後` / `擋 readiness`（新 pod 在做完前不會 ready）。
6. 條目固定四格，只列非空的：**設定**（fork 的 config.yaml）/ **資料**（回填、重讀）/
   **k8s · CI 側**（他們自己維護的 manifests 與 CI 要跟著加的）/ **確認做完**。
   每個動作寫**指令 + 何時 + 為什麼 + 漏跑的症狀**。標題是 `日期 · merge commit · #PR 標題`；
   新條目在合併前寫，merge commit 還不知道時可以省略，日期與 PR 編號一定要有。

一次盤點所有會回填的 model（dry-run 不寫回，安全）：

```bash
uv run python scripts/run_migrate.py --dry-run \
  workspace-file doc-chunk cluster-member source-doc card-gen-run \
  graph-claim graph-mention graph-entity graph-entity-link graph-relationship
```

已在最新版的 model 回一整排 `skipped`。dry-run 顯示有待遷移、但這份手冊沒列它的 model
（例如 `kb-chat`），跑了無害、不跑也不影響行為。**`notification` 不在清單裡是故意的**——
它是「不要回填」的那一種，見 [#803](#pr-803)。

---

## 條目

### 2026-06-18 · 3371253c · #120 VLM 描述完再用 LLM 整理一次 {#pr-120}

**設定**

- `kb.vlm_format_llm`（新；預設 `null` = 沿用 `kb.retrieval_llm`）。
  為什麼：小 VLM 讀圖準，但吐出來的是散文，chunker 會截掉；現在圖片／PDF 視覺頁／投影片經 VLM
  描述後，再用文字 LLM 整理成 Markdown 才切塊。
  不動設定：`kb.vlm_llm` 與 `kb.retrieval_llm` 預設都是內建 preset，所以**每張圖的索引多一次 LLM
  呼叫**。沒有單獨關掉這一段的值——只有 `kb.retrieval_llm: null`（連帶關掉 multi-query／HyDE／rerank）
  才會跳過。

### 2026-06-24 · d23c4c1f · #211 Postgres 連不上時 10 秒內開機失敗 {#pr-211}

**設定**

- `filestore.pg_connect_timeout`（新；預設 `10` 秒；`0` = 舊行為）。
  為什麼：specstar 的 engine 沒設 timeout，PG 不通時第一次連線會安靜地卡幾分鐘；現在 10 秒就以
  明確錯誤結束。`pg_dsn` 裡自己寫的 `connect_timeout=` 優先。

### 2026-06-25 · cb82a1a5 · #212 sticky routing 的 key 換成新 URL {#pr-212}

**k8s · CI 側**（多 pod 且用 ingress-nginx sticky routing 的部署）

- ingress 的 snippet 從抓 `/investigations/<id>` 改抓 `/a/<app>/items/<id>` 與 `/kb/chats/<id>`，
  照 `kubernetes/base/ingress.yaml`。
  為什麼：路由改版後舊 regex 不再命中，同一個 item 的請求散到不同 pod（#202 聊天空白）。
  漏改的症狀：多 pod 下聊天偶爾空白、檔案「時有時無」。

### 2026-06-25 · e21e2031 · #213 KB agent 每回合最多搜 3 次 {#pr-213}

**設定**

- `kb.max_searches_per_turn`（新；預設 `3`；`null` = 不限）。
  不動設定：KB 聊天與 `ask_knowledge_base` 每回合 `kb_search` 最多 3 次（以前不限），用完會被要求
  用手上的資料作答。

### 2026-06-26 · ec07eb77 · #257 單檔上傳上限 2 GiB {#pr-257}

**設定**

- `filestore.max_file_size`（新；預設 2 GiB；`0` = 不限）。不動設定：超過 2 GiB 的單檔上傳被拒。
  ⚠️ ingress 自己的 body 上限是另一回事（預設 1 MB），見 [#563](#pr-563)。

### 2026-06-26 · 7b82131f #238 + d3beaefb #260 · hosted sandbox：獨立的 `sandbox-host` 服務（opt-in） {#pr-260}

**k8s · CI 側**（只在 `sandbox.kind: http` 時——production 用的就是這種）

- 多一個要自己部署的服務：`sandbox-host`（獨立的 uv 專案 `sandbox-host/`，自己的 Dockerfile 與映像），
  範例在 `deploy/sandbox-host.example.yaml`（Deployment + Service，`SANDBOX_HOST_*` 環境變數，
  `/readyz` / `/healthz` 探針）。app 這邊設 `sandbox.kind: http` + `sandbox.http.base_url` 指向它。
  之後每個動到 `sandbox-host/` 的 PR，兩邊都要一起升（CI 永遠重建兩個映像）；不為版本歪斜設計相容模式。
  不用 hosted sandbox 的部署：什麼都不用做。

### 2026-06-26 · 26406008 · #266 `kb.embedder.num_retries` 拿掉了 {#pr-266}

**設定**（⚠️ 拒絕開機）

- 設過 `kb.embedder.num_retries` 的 config，升級後開機失敗、訊息點名這個 key：**刪掉它**。
  為什麼：embedding 的重試改成內建的退避後切換下一個 replica（`kb.embedder.fallbacks`），不再是旋鈕。

### 2026-06-27 · 9600ef3f · #276 位置過濾與檔名索引（doc-chunk v3、source-doc v3） {#pr-276}

**資料**（rollout 後）

1. `uv run python scripts/run_migrate.py --dry-run doc-chunk source-doc` → 沒 `failed` 再去掉 `--dry-run`。
   為什麼：`DocChunk.provenance`（page／slide／sheet／row／line）與 `SourceDoc.path` 進索引，
   「分析某檔第 N 頁」的過濾與「檔名 → 文件」的解析改走索引；索引只在寫入當下抽取，舊列沒有。
   漏跑的症狀：對舊文件指定頁碼的搜尋回空；用檔名找舊文件找不到。

**確認做完**：dry-run 兩個 model 全 `skipped`。

### 2026-06-27 · b982f065 · #278 workspace 額度 20 GiB、孤兒 blob 回收、workspace-file v2 {#pr-278}

**設定**

- `filestore.workspace_quota`（新；預設 20 GiB；`0` = 不限）。不動設定：一個 workspace 的檔案總量
  超過 20 GiB 時，上傳／編輯回 **507**。
- `filestore.gc_interval_sec`（`3600`）／`gc_t1`（`1h`）／`gc_t2`（`24h`）。不動設定：每小時掃一次
  孤兒 blob（已刪檔案的內容），新於 `gc_t1` 的不動、先隔離 `gc_t2` 再永久刪除。`gc_interval_sec: 0` 不掃。
  （#813 起這件事改由 worker 做，見 [#813](#pr-813)。）

**資料**（rollout 後）

1. `uv run python scripts/run_migrate.py workspace-file`。
   為什麼：v2 把 `content.size` 進索引，耐久儲存那邊的用量加總用它（#538 之後用量先問活著的 sandbox，
   sandbox 冷的時候才退回這個加總）。漏跑的症狀：sandbox 冷的時候，升級前的檔案不計入用量，額度低估。
   ⚠️ 今天跑會直接帶到 v3，而 v3 有「必須打新 pod」的規矩——照 [#668](#pr-668) 的做法跑。

### 2026-06-27 · cfa12349 · #293 source-doc v4：`token_count` {#pr-293}

**資料**（rollout 後）

1. `uv run python scripts/run_migrate.py source-doc`。
   為什麼：每份文件記 token 數並進索引，collection 頁的 `token_total` 用索引加總；這個 step 會真的替
   舊列算出 `token_count`（不是 no-op）。漏跑的症狀：舊文件不計入 collection 的 token 總量。

### 2026-06-28 · 94fc5b75 · #314 job worker 可以拆出 API {#pr-314}

**設定**

- `server.run_consumers`（新；預設 `true` = 跟以前一樣，API 自己消化所有 job）。

**k8s · CI 側**

- repo 的 base manifests 從這版起是 `RUN_CONSUMERS: "false"` + `workers.yaml`（每個 JobType 一個
  Deployment：`index` / `wiki` / `card-gen` / `sanity`）。**照抄 base 的部署要一併加 worker
  Deployments**；不拆的部署什麼都不用做。
  漏加的症狀：**沒有任何錯誤**——那類 job 被接受、入列，然後永遠 pending。
  之後每新增一種 JobType 都有自己的一條：[#540 eval](#pr-540)、[#553 graph](#pr-553)、
  [#706 kb-import](#pr-706)、[#813 blob-gc](#pr-813)。

**確認做完**：`run_consumers: false` 時，`kubectl get deploy | grep rca-worker-` 每種 JobType 各一個。

### 2026-07-01 · b9b7dd0a · #359 code collection 每天 03:00 自動同步 {#pr-359}

**設定**

- `kb.git.daily_sync`（新；預設 `"03:00"`，伺服器本地時間；`""`／`null` = 關）。
  不動設定：每個有 `git_url` 的 collection 每天 03:00 重新 sync。取代原本每個 collection 自己的
  `sync_interval_hours`（那個欄位還在，但不再生效）。

### 2026-07-01 · 6cb1c19d · #360 sticky snippet 改成 server-snippet {#pr-360}

**k8s · CI 側**（多 pod 且用 ingress-nginx sticky routing 的部署）

- `configuration-snippet` → `server-snippet`，`$uri` → `$request_uri`，照 `kubernetes/base/ingress.yaml`。
  為什麼：sub-path 部署的 overlay 會在 location 裡 `rewrite … break;`，`break` 之後的 `set`／`if`
  都不會跑，key 停在 `$host`，每個 item 靜默退回 round-robin（root 部署沒有 rewrite，所以看不出來）。
  server-snippet 在選 location 之前執行、`$request_uri` 是原始請求行，不受 rewrite 影響。
  確認：`kubectl exec -n ingress-nginx <controller> -- cat /etc/nginx/nginx.conf | grep -n -E 'rca_ws_key|rewrite .* break'`
  ——`set $rca_ws_key` 要在 server 層、key 是 item／chat id 不是 `$host`。

### 2026-07-03 · b6a12d41 · #406 source-doc v6：狀態與內容欄位進索引 {#pr-406}

**資料**（rollout 後）

1. `uv run python scripts/run_migrate.py source-doc`。
   為什麼：文件列表改成只查索引、不撈整列（`status` / `status_detail` / `content.content_type` /
   `content.file_id`），狀態摘要用 `status` 分組計數。
   漏跑的症狀：升級前的文件在 `documents/status` 的摘要裡沒有狀態、被少算；wiki 重建回報的
   「涵蓋幾份」也少算。

### 2026-07-04 · bee5c29f · #420 source-doc v7：權限鏡像 {#pr-420}

**資料**（rollout 後，⚠️ **盡快**）

1. `uv run python scripts/run_migrate.py source-doc`。
   為什麼：文件在 storage 層的可見性改由文件自己帶的 collection 權限鏡像決定
   （`collection_visibility` / `collection_read_meta` / `collection_created_by`），因為 auto-CRUD 的
   `GET /api/source-doc/{id}` 沒有路由守衛，只靠這一層。
   漏跑的症狀：沒有鏡像的舊列在 storage 層**被當成 public**——私有 collection 裡升級前就存在的
   文件，透過 auto-CRUD 路由對沒權限的人可讀（走 `/api/kb/...` 的入口有 collection 層檢查，不受影響）。

**確認做完**：dry-run `source-doc` 全 `skipped`。

### 2026-07-06 · be6cb3bd · #486 wiki 每天 04:00 沉思 {#pr-486}

**設定**

- `kb.wiki.reflect_daily`（新；預設 `"04:00"`；`""`／`null` = 關）。
  不動設定：每個 prose wiki collection 每天 04:00 跑一次 reflection（用 `kb.wiki.llm`——LLM 成本）。

### 2026-07-07 · 880d1d9e · #490 doc-chunk v4：內容 hash {#pr-490}

**資料**（可選）

- 不需要 migrate（step 只會把舊列的 hash 重抽成空字串）。去重回收與「同內容多路徑」的展開只涵蓋
  升級後索引的 chunk；要涵蓋舊的，重讀 collection（`POST /api/kb/collections/<id>/reindex`）。

### 2026-07-08 · 3c5efadb #496 + 3e3f20f0 #502 · sandbox durable 走 NFS（opt-in），設定 key 搬家 {#pr-502}

**設定**（⚠️ 拒絕開機）

- #496 短暫加在 `filestore.migrate_from` / `filestore.nfs_root` 的兩個 key，#502 搬到
  `sandbox.durable.migrate_from` / `sandbox.durable.nfs_root`（並加 `sandbox.durable.kind`）。
  設在舊位置的 config 升級後開機失敗、訊息點名 key。

**k8s · CI 側**（只在採用 host-managed durable 時）

- sandbox-host 要 `SANDBOX_HOST_NFS_ROOT` 並掛同一個 workspaces PVC；app 設
  `sandbox.http.host_managed_durable: true` + `sandbox.durable.kind: nfs_tree` 指同一棵樹。範例的 host
  timeout 一併調大（`SANDBOX_HOST_EXEC_TIMEOUT=3600` / `LOG_TIMEOUT=1500` / `IDLE_TTL=36000`），ingress
  `proxy-read-timeout: "3600"`。不採用：什麼都不用做。

### 2026-07-13 · a88aee2f · #507 待審核 inbox 的分群、card-gen-run v2 {#pr-507}

**設定**

- `kb.cluster.*`（新：`cluster_tau` 0.9 / `merge_tau` 0.95 / `suppress_tau` 0.92 / `update_tau` 0.8 /
  `sweep_interval_seconds` 900）。不動設定：每 900 秒對每個 collection 跑一次 cluster sweep（補投影
  候選 + 折疊分群）——在這版到 [#804](#pr-804) 之間它跑在 **API pod** 上、每顆 pod 各跑一遍並打
  embedding，正是 API OOM 的來源；#804 起改由 card-gen worker 做。

**資料**（rollout 後）

1. `uv run python scripts/run_migrate.py card-gen-run`。
   為什麼：`collection_id` 新進索引，每個 collection 的待審核分頁改查 `(collection_id, status)` 而不是
   掃全部。漏跑的症狀：全域 inbox 看得到、但 **collection 分頁看不到升級前的待審 run**（少列，不是遺失）。

### 2026-07-17 · 297f5fa1 · #524 specstar 0.12.1：向量不再進 `indexed_data`（doc-chunk v5、cluster-member v1） {#pr-524}

**資料**（rollout 後、低流量時段）

1. `uv run python scripts/run_migrate.py --dry-run doc-chunk cluster-member` → 正式跑。
   為什麼：0.12.1 起 `Vector` 欄位不再複製進 `indexed_data`（它有自己的 pgvector 欄），但只對新寫入生效；
   舊列的 JSONB 還帶著整條向量、被 GIN 逐元素索引，這就是文件列表慢的元凶。
   漏跑的症狀：文件列表慢；`cluster-member` 以前完全沒有 `Schema`，這版補上 `None → v1`。
2. 回收索引空間（腳本會印出來）：`REINDEX TABLE CONCURRENTLY doc_chunk_meta;` /
   `REINDEX TABLE CONCURRENTLY cluster_member_meta;`——migrate 完查詢就快了，但索引檔要 REINDEX 才縮。
   範圍：清的是 `indexed_data` 這個 JSONB 欄；每列完整 meta（含向量）在 `data` BYTEA 欄還有一份，刻意不動。

### 2026-07-20 · 495f6c9c #536 + 05d6f9fb #540 · 檢索品質 eval：`eval` JobType {#pr-540}

**k8s · CI 側**（只在 `run_consumers: false` 且要用 eval 時）

- 多一種 JobType `eval` → `rca-worker-eval` Deployment（`python -m workspace_app.worker eval`）。
  `cronjob-eval.yaml` 是每天 02:00 UTC 對 `POST /api/eval-job` 打一次的 CronJob，要不要排是你的事
  （功能本身要先有 eval 資料集，見 deployment.md §13）。漏加 worker 的症狀：eval job 永遠 pending。

### 2026-07-20 · e2335bd6 · #539 doc-chunk v6：關鍵字檢索走 `text` 三連字索引 {#pr-539}

**資料**（rollout 後、低流量時段）

1. `uv run python scripts/run_migrate.py --dry-run doc-chunk` → 正式跑。
   為什麼：檢索不再整包載入整個 collection，BM25 那半改用 `DocChunk.text` 上的 pg_trgm 索引先縮小候選集；
   `text` 要先被抽進該列的 `indexed_data` 索引才看得到它。
   漏跑的症狀：**舊 chunk 對關鍵字檢索隱形**——語意（向量）檢索照常、新上傳的立即正常，所以不會噴錯，
   只是舊文件的關鍵字命中率安靜地掉下去。
2. 之後可跑 `REINDEX TABLE CONCURRENTLY doc_chunk_meta;` 讓重寫後的索引緊實（這次 `indexed_data` 是變大的，
   不是為了回收空間）。

**DB**：pg_trgm 擴充與 GIN 由 specstar **每次開機**確保存在（`CREATE EXTENSION IF NOT EXISTS pg_trgm`），
DB role 要有建擴充的權限，或事先由 DBA 建好。

### 2026-07-20 · ed0a0af1 · #552 單一工具結果的絕對上限 {#pr-552}

**設定**

- `exec.tool_output_max_chars`（新；預設 `200_000`）。不動設定：任何一個工具的單次結果超過 20 萬字元
  一律截斷（以前只有各工具自己的上限）。小窗口模型可以調低。

### 2026-07-20 · 6765401e · #553 knowledge graph：`graph` JobType {#pr-553}

**k8s · CI 側**（只在 `run_consumers: false` 且有 collection 開 `use_graph` 時）

- 多一種 JobType `graph` → `rca-worker-graph` Deployment；`cronjob-graph.yaml` 是每週六 03:00
  （Asia/Taipei，[#627](https://github.com/HYChou0515/ai-workspace/pull/627) 起）對 `POST /api/graph-job`
  打一次的 CronJob。漏加 worker 的症狀：graph job 永遠 pending。同版把 specstar 釘到 `0.13.0a1`。

### 2026-07-20 · 87b4dbe5 · #563 ingress 的上傳 body 上限 {#pr-563}

**k8s · CI 側**（ingress-nginx）

- `nginx.ingress.kubernetes.io/proxy-body-size: "2g"` + `proxy-request-buffering: "off"`，照
  `kubernetes/base/ingress.yaml`。為什麼：ingress-nginx 預設 1 MB，幾 MB 的附件會被 proxy 以 413 擋掉、
  永遠到不了 app——使用者看到「超過大小上限」，伺服器 log 什麼都沒有，而 app 以為自己在管的是 2 GiB。
  buffering 關掉是讓 app 能邊收邊擋，不必先把 2 GB 整包吐到 proxy 的磁碟。

### 2026-07-20 · c1c5d9e9 · #566 `sandbox.max_workspace_bytes` 拿掉了 {#pr-566}

**設定**（⚠️ 拒絕開機）

- 設過 `sandbox.max_workspace_bytes` 的 config，升級後開機失敗：**刪掉它**（`SANDBOX_MAX_WORKSPACE_BYTES`
  這個 configmap key 也一併拿掉）。為什麼：#538 之後 workspace 大小只有一個上限——`filestore.workspace_quota`。

### 2026-07-21 · e37fe7d2 · #586 graph-claim v2：比對鍵重算 {#pr-586}

**資料**（rollout 後；沒有 collection 開 `use_graph` 的部署這張表是空的，跑一次當確認即可）

1. `uv run python scripts/run_migrate.py graph-claim`。step 是**重算衍生欄位**（`_renormalize_claim`），
   不是 no-op：依當前規則重算 `norm_*` 比對鍵。漏跑的症狀：舊列還帶舊規則的鍵，本該視為同一件事的兩列
   可能還是兩件。（[#631](#pr-631) 再升 v3、[#689](#pr-689) 一起跑就好。）

### 2026-07-22 · 8f02c7bd · #595 graph-mention v1：比對鍵重算 {#pr-595}

**資料**（rollout 後；同上，空表跑一次即可）

1. `uv run python scripts/run_migrate.py graph-mention`。`None → v1` 是 `_renormalize_mention`，
   重算 `norm_surface` / `norm_kind`。（[#689](#pr-689) 再升 v2，一起跑就好。）

### 2026-07-22 · b1f7155e · #606 「有答案但你沒權限」預設開 {#pr-606}

**設定**

- `kb.disclosure.enabled`（新；預設 `true`）。不動設定：每個 KB 回合多跑一次只算分數的探測，對使用者
  看不到的 collection 若有相關內容，回答會列出「被保留的來源」與申請存取的入口。`false` = 整個關掉
  （每次 `kb_search` 少一次 ANN 查詢；聊天內的開關只能再縮，不能反過來打開）。

### 2026-07-24 · eafba245 · #632 對話記憶不再硬切 40 則／24k token {#pr-632}

**設定**（⚠️ 不動設定行為就變）

- `history.max_messages` 預設 `40 → 0`（關）、`history.max_context_tokens` 預設 `24_000 → 0`（關），
  新增 `history.context_limit`（預設 `null` = 每回合自己解析端點的窗口）。
  為什麼：舊預設是替假想的 32K 窗口寫的，而且用 `chars // 4` 估算、對繁中低估 3.6 倍——實測一場帶工具
  輸出的工作只記得住最後 ~3 回合。現在預算從端點真實窗口扣掉 system prompt 與工具 schema 後推得。
  自己設過這兩個值的部署不受影響（仍是硬上限）。
  ⚠️ 解不出窗口的部署（自架模型掛在 proxy 後面、用別名）**歷史永不裁切、自動壓縮永不執行**——怎麼分辨、
  什麼時候該手動設 `history.context_limit`，見
  [deployment.md §11「上下文窗口與自動壓縮」](deployment.md#上下文窗口與自動壓縮誰決定怎麼確認什麼時候才需要你出手)
  與 [#767](#pr-767)。

### 2026-07-24 · 1c7f83f7 · #631 graph-claim v3 {#pr-631}

**資料**（rollout 後）：`uv run python scripts/run_migrate.py graph-claim`——仍是重算比對鍵的 step
（`norm_subject` / `norm_attribute` / `norm_period` / `norm_unit`）。[#689](#pr-689) 一起跑。

### 2026-07-29 · 876b8e81 · #668 workspace-file v3：`path` 索引 — 🚨 這一條會擋住 rollout {#pr-668}

檔案樹和每一份 entity 列表都走 `ls(prefix=…)`，它把查詢下推到 `WorkspaceFile.path` 索引（不下推的話
3000 檔的 workspace 列一次 796 ms，一次互動叫十次）。沒回填的舊列答不出 `path` 述詞——**檔案樹、entity
列表在資料完好的情況下讀成空的**，三個 replica 滾動時新舊 pod 答案不同、畫面閃爍。所以：

- **`/api/readyz` 在回填完成前一律 503**（`prefix_index_ready()`），readinessProbe 指著它。忘記跑不會看到
  「慢」，會看到**新 pod 永遠不 ready、rollout 停住**。liveness 故意不看它，讓那些 pod 活著給你跑回填。

**k8s · CI 側**

- Deployment 的 readinessProbe 從 `/openapi.json` 改成 **`/api/readyz`**（照 `kubernetes/base/deployment.yaml`）。
  沒改的症狀：新 pod 一 ready 就端出空的檔案樹。

**資料**（rollout 後、**擋 readiness**）

1. 先 rollout。新 pod 起來但不 ready，舊 pod 繼續服務，沒有中斷。
2. ⚠️ **回填必須打新 pod，不能打 Service**：Service 只導流量給 ready 的 pod，卡住時 ready 的全是舊 pod；
   舊 pod 認得的最新版是 v2（沒有 `path`），migrate 只把列帶到「該 pod 認得的最新版」——打 Service
   會**回報一整排成功、什麼都沒改**。
   ```bash
   kubectl get pods -l app=rca-app                      # 找一個新的、還沒 ready 的
   kubectl port-forward pod/<新 pod> 8000:8000           # 不經過 Service，不 ready 也連得到
   uv run python scripts/run_migrate.py --dry-run --base-url http://localhost:8000 workspace-file
   uv run python scripts/run_migrate.py           --base-url http://localhost:8000 workspace-file
   ```
3. 新 pod 自己會變 ready，rollout 走完，不用重啟。

**確認做完**：`curl -i http://localhost:8000/api/readyz` → `200 ok`（`503 workspace-file path index not
backfilled` = 還沒）。全新安裝不受影響（沒有舊列時 readyz 一開始就綠）。

### 2026-08-03 · 4f8019ac · #689 graph 的五個 model {#pr-689}

**資料**（rollout 後；沒開 `use_graph` 的部署五張表都是空的，跑一次當確認）

```bash
uv run python scripts/run_migrate.py --dry-run graph-claim graph-mention graph-entity graph-entity-link graph-relationship
uv run python scripts/run_migrate.py           graph-claim graph-mention graph-entity graph-entity-link graph-relationship
```

| model | 這版 | step 的形狀 | 漏跑的症狀 |
| --- | --- | --- | --- |
| `graph-claim` | v3 | 重算比對鍵 | 舊列停在舊規則，本該同一件事的兩列可能還是兩件 |
| `graph-mention` | v2 | `None → v1` 重算比對鍵、`v1 → v2` 重抽索引 | 同上；走訪也得逐列解 blob |
| `graph-entity` / `graph-entity-link` / `graph-relationship` | v1 | 重抽索引 | 週期 pass 走訪要逐列解 blob，**慢但不錯**——讀取端對沒回填的列會退回讀 blob，這三個是加速不是正確性閘門 |

### 2026-08-25 · 6d1e67e3 · #706 大封存包非同步匯入：`kb-import` JobType {#pr-706}

**k8s · CI 側**（只在 `run_consumers: false` 時）

- 多一種 JobType `kb-import` → `rca-worker-kb-import` Deployment（`python -m workspace_app.worker kb-import`）。
  漏加的症狀：`POST /api/kb/collections/imports` 回 202 之後永遠 `written == 0`。同步匯入那條路
  （`/kb/collections/import`）不受影響、網頁目前也只打同步那條。

### 2026-09-03 · 6ba0cbeb · #759 碰到 429 改成等，不是燒完重試 {#pr-759}

**設定**（⚠️ 不動設定行為就變）

- `failover.rate_limit_budget_s`（新；預設 `7200`）。不動設定：agent 碰到 429 從「快速燒完重試然後放棄」
  變成「在原端點等它聲明的窗口」，同一次 agent run 共用一池、上限 2 小時；畫面出現「請求過於頻繁，N 秒後
  自動重試」。單一端點（沒設 `fallbacks`）的部署也適用。設 `0` 回到舊行為。

### 2026-09-04 · 941476f9 · #776 profile 自帶 python 環境：uv 快取活得比 sandbox 久 {#pr-776}

**設定**

- `sandbox.uv_cache_max_bytes`（新；預設 `null` = **無上限、不淘汰**）。不動設定：profile 帶
  `pyproject.toml` 時，每個 item 一份 uv 下載快取放在 sandbox 目錄旁、冷啟動不必重抓；代價是**磁碟只增不減**
  （app 端那份在共用 scratch 磁碟區，連 rollout 都不會清）。設一個數字，idle tick 就按最近最少使用淘汰，
  活著的 sandbox 正在用的那份永遠不動。app 端這顆只對 `kind: local` 且沒套 userns jail 時有作用。

**k8s · CI 側**（`kind: http`）

- host 那份快取由 `SANDBOX_HOST_UV_CACHE_MAX_BYTES` 管（`deploy/sandbox-host.example.yaml`），不設同樣無上限，
  而它落在 host pod 的 ephemeral 磁碟——塞爆會被 kubelet 驅逐。

### 2026-09-07 · edd875d3 · #788 工作流單步 10 分鐘上限；頁面排程 {#pr-788}

**設定**（⚠️ 不動設定行為就變）

- `server.workflow_step_timeout_sec`（新；預設 `600`）。不動設定：工作流裡單一 agent 步驟從「沒有上限」
  變成 **10 分鐘就中止那一步**（訊息寫出秒數）。機制本來就在，但從沒有地方把值傳進去，所以每個部署
  實際上都跑在無上限。有合法長於十分鐘步驟的部署要調大，或 `0` 回到舊行為。
- `server.trigger_check_interval_sec`（既有，語意擴大）：現在同時決定頁面自己寫的 `schedules.json`
  會不會被掃到。預設仍 `0` = **完全不掃、沒有錯誤訊息**——「頁面排程」在沒開這顆的部署上存在但永遠不動。
  要用就設個秒數（60 是合理起點）。
- `server.max_page_schedules`（1000）與 `server.notification_channel`（`""`）：不設完全不變。

### 2026-09-09 · dce082a0 · #767 proxy 後面也能推出窗口上限 {#pr-767}

**設定**（⚠️ 不動設定行為就變）

- `history.max_tokens_window_ratio`（新；預設 `0.8`）。不動設定：窗口解析多了一段「問 proxy 的
  `/model/info`」。原本前四段全滅、上限只能是 `unknown` 的部署（自架模型掛 litellm proxy 後面、用任意別名），
  `unknown` 的意思是歷史從不裁切、自動壓縮從不執行；現在若 proxy 只答得出 `max_tokens`，用它 × 0.8 推出一個
  標記為估計的上限，裁切與壓縮開始運作。推導值裝不下已知開銷時退回 `unknown`（舊行為）。
  ⚠️ 沒有「設 0 回到舊行為」——載入要求 `0 < ratio <= 1`；要完全不走推導就明確設 `history.context_limit`。

### 2026-09-14 · 91bb0fdd · #803 WUI：`notification.outbound` — ⚠️ **不要回填** {#pr-803}

`Notification` 多了 `outbound` 索引（`""` 待送 / `sent` / 無法送達），外送掃描查 `outbound == ""`。
這個欄位出現之前寫下的每一列都對不上任何值，掃描永遠看不到它們——**這正是要的**：一個部署第一次接上
email 通道（`server.notification_channel`）時，平台歷史上每一則通知不會在那一分鐘被寄出去一遍。
所以 `notification` 不在盤點清單裡；dry-run 看到它有待遷移的列，**不要跑**。真要補送某段期間的通知，
那是一次有明確時間範圍的一次性動作，不是 `migrate/execute`。

### 2026-09-15 · 09bbfb54 · #804 API 上不再做 cluster sweep 與開機索引 {#pr-804}

**設定**（⚠️ 不動設定行為就變——而且是 API OOM 的修法）

- `kb.cluster.sweep_interval_seconds` / `merge_tau`（既有，語意改變）：待審核 inbox 的 cluster sweep
  **不再在 API pod 上執行**。以前每顆 API pod 各自每 900 秒把每個 collection 的全部 `ClusterMember`
  讀進記憶體、逐候選打 embedding——零流量也照跑、N 顆 pod 跑 N 遍。現在 API 每個 interval 只 **enqueue**
  一個 card-gen job（fleet-wide 一個 window 一顆 pod 問；同 collection 已有 queued/running 的合併；每次
  sweep 前硬刪同 collection 其他終態列，job row 不隨 uptime 長），工作在 **card-gen worker** 上跑。
- `server.trigger_check_interval_sec`（既有）：多 pod 時它也是掃描的 window——每個 window 只有第一顆 tick
  的 pod 真的重讀所有 `triggers.json` / `schedules.json`。單 pod 完全不變；事後調大這個值是安全的。
- （無選項）開機的 help collection：文件只 **store**，變動的交給 index queue（`system` 名下一份一個 job），
  不再在 API 上 inline chunk + embed。

**k8s · CI 側**（`run_consumers: false` 的部署）

- **最少要跑 `rca-worker-index` 與 `rca-worker-card-gen`**。漏跑的症狀：help 文件停在 `indexing`、
  `/help` 的 KB chat 搜不到它們；cluster sweep job 永遠 pending（以前 API 默默做掉）。

### 2026-09-15 · d50c7229 · #806 RAG 前後文、資料夾範圍搜尋、讀原文（source-doc v10） {#pr-806}

**設定**

- `kb.retrieval.context_chars`（新；預設 `2000`；`0` = 關；`null`／負數**拒絕開機**）。
  ⚠️ 不動設定行為就變：每個檢索命中前後各至少多帶 2000 字元原文（整塊 chunk、可跨到文件樹上的鄰居檔案），
  rerank 的 prompt 隨之變長、agent 看到的段落變寬；引用 `[n]` 仍指命中處。
- `kb.retrieval.rerank_context_chars`（新；預設 `4000`；`null` = 不封頂；`0` = 只看命中）：rerank 每個候選
  最多看到的前後文。不設它 `context_chars` 會讓 rerank prompt 長 4×（英）／27×（中）。
- 自訂 kb preset 若釘死 `allowed_tools`：加 `kb_grep`、`read_page`、`read_lines`（內建 `kb-*` 已含）。
  漏加的症狀：kb prompt 無條件描述這三個工具，模型被告知能用卻呼叫不到。

**資料**（rollout 後）

1. `uv run python scripts/run_migrate.py --dry-run source-doc` → 正式跑（v10：`path` 索引正式上帳）。
   為什麼：「限定資料夾」搜尋用 `path.starts_with` 解析範圍。漏跑的症狀：舊文件不在任何資料夾裡——是**少列**
   不是排錯。
2. **每個 collection 重讀一次**：`POST /api/kb/collections/<id>/reindex`（#390 的 index cache 會先被丟掉）。
   為什麼：管線路徑的 chunk 位移一直是相對於該頁／該列的 Document、又用 `text.find`（第一次出現）定位，
   master 上沒人拿位移切原文所以沒發現；這版起位移正確，但**舊索引的位移仍是錯的**。
   漏跑的症狀：`read_page(N≥2)` 顯示第 1 頁的文字、`kb_grep` 找不到第 2 頁起的字、引用卡的前後文接錯段、
   P6 切出的長 Markdown 視窗沒有頁碼。`DocChunk.unit_start` 是新欄位（msgspec 預設值），**不需要** migrate。

**確認做完**：dry-run `source-doc` 全 `skipped`；任一多頁 PDF 的 `read_page(2)` 顯示第 2 頁。

### 2026-09-15 · ced4659d · #805 item 層級排程：AI 把自製 workflow 放上時鐘 {#pr-805}

**設定**（沒有新旋鈕，兩顆既有旋鈕的作用面變大）

- `server.trigger_check_interval_sec` 現在也決定 **AI 用 `save_schedules` 設的排程**會不會跑（預設 `0` 仍不掃，
  但工具回覆和 Workflows 面板會大聲說「這個部署沒開排程」）；`server.max_page_schedules` 同一個上限也套在
  item 層級那份檔案、AI 存檔時就擋。
- ⚠️ 不在 config 的行為改變：頁面 `startRun` 與排程的 `run` 從「只認 profile 宣告的 workflow」變成
  「profile ∪ item 自己 `.workflows/` 的」；沒有設定能回到舊判準。

### 2026-09-16 · e096f463 · #810 workflow.json 的 `agent` / `sandbox` 步驟 `cache` 必填 {#pr-810}

**行為**（⚠️ 破壞性，改不到的是使用者 workspace 裡的檔案）

- 既有省略 `cache` 的 `.workflows/*.json` 部署後**解析失敗**——不再靜默消失：Workflows 面板列出它和原因
  （`steps[N]: \`cache\` is required — …`，沒有 Run）、排程列標「解析失敗」不開火、`save_schedules` 拒絕。
  修法是由 AI／人用 `save_workflow` 補上每步的 `cache`（`false` 給會碰外界／有副作用的，`true` 給輸入全在
  arguments 的）。起因：排程跑的 workflow 每步預設 `true`，第二次開火每步拿收據、什麼都不做。
  repo 內的範本與 playground `dsl` profile 已補齊。

### 2026-09-16 · a4b890b7 · #809 沒有 request 的 turn 要憑證：`IRequestEnv.env_without_request` {#pr-809}

**k8s · CI 側**（只在部署有自訂 `server.request_env` 實作時）

- 介面多了 `env_without_request(user_id, item_id)`，給**沒有人按送出**的 turn（排程、事件觸發、頁面按鈕
  起的 workflow、goal 續跑）要變數。預設回空 = 那些 turn 跟以前一樣拿不到 request env；要讓它們有憑證就實作它。
  ⚠️ `user_id` 是**歸屬**不是在場（排程與 WUI 按鈕的 run 記在 item **owner** 名下，owner 是可改的自由文字）：
  按 `user_id` 發個人憑證等於把 owner 的憑證交給每個能寫排程／能按按鈕的人；用共用服務帳號，或自己依
  `item_id` 加規則。

### 2026-09-18 · cd96c6ef · #813 孤兒 blob 回收改成 job：`blob-gc` JobType {#pr-813}

**行為**（⚠️ 不動設定行為就變——API OOM 的另一個修法）

- reconcile（specstar `gc(mode="reconcile")`）**不再在 API pod 上執行**。以前搶到 lease 的那顆 API pod 每
  `gc_interval_sec` 把每個 blob-capable model 的全部 ResourceMeta（含向量）一次載進記憶體——pod 死前最後一行
  就是 `blob-gc: won lease`。現在 API 每個 window 只 enqueue 一個 `BlobGcJob`，工作在 `blob-gc` worker 上跑。
- **跑完的 pass 會回收 job log**：specstar 把每個 job 的 log 存成沒人引用的 keyed blob，reconcile 把它當孤兒，
  超過 `gc_t1` 隔離、`gc_t2` 後刪（約 25h）；`GET /api/<job-model>/<id>/logs` 對更舊的 job 回 204。
  master 上 pass 在 prod 從沒跑完（OOM），log 活著是意外。

**k8s · CI 側**（`run_consumers: false` 的部署）

- 多一種 JobType `blob-gc` → `rca-worker-blob-gc` Deployment（`python -m workspace_app.worker blob-gc`）。
  **這個 worker 開機組的是 API 自己那整套**（`workspace_app.__main__.build_app`，只組不 serve）：reconcile 只從
  已註冊的 model 算 live 集合，拿部分註冊表跑會把缺的 model 引用的 blob 隔離、`gc_t2` 後刪掉；每次 enqueue
  都帶 API 端的 model 清單，runner 少任何一個就**拒跑**（job FAILED、log 點名）。記憶體要照「一次持有單一
  model 的全部 ResourceMeta（doc-chunk / cluster-member 每一列都帶向量）」給，`workers.yaml` 的註解有寫；
  它同時掛 `data` 與 `scratch` 兩個磁碟區。
  漏加的症狀：孤兒 blob 不再回收（只長不消，不掉資料）；`/monitor/summary` 的 `blob_gc` / `ws_census` 趨勢停住。
- 同 PR 順手修的既有 bug：刪「從沒宣告 schedule 的 item」在 lifespan 有跑的部署會 500「failed partway」
  （2026-09-09 的 48f09a55 起）；現在不會。另外四個協調 model 改在 `create_app` 無條件註冊——Postgres 上多兩個
  永遠空的 model（各三張表），無害。

---

## 附錄 A：資料回填的機制（specstar 為什麼不會自己補）

有些升版會改變「資料在資料庫裡的儲存形狀」，但 **specstar 只在寫入當下**把一列的 `indexed_data`
算好，之後**不會自動回填**。既有的資料列會停在舊形狀，直到每一列被**重新寫過一次**。

> 一句話：**升版只讓「未來的寫入」變乾淨；既有的列要靠 migrate 重寫才會跟上。**

### 什麼時候需要跑

當一次部署做了下面任一件事，既有列就會落後：

- **新增了一個索引**到既有 model（例如替某欄位加上 `IndexableField`）。舊列在索引加入前就寫好了，
  不會出現在新索引裡：聚合時少算、以那個欄位過濾時**少列**。
- **改變了 `indexed_data` 的算法**（例如 specstar 0.12.1 不再把 `Vector` 複製進去，[#524](#pr-524)）。
- **改變了「衍生欄位」的算法**（例如 graph 的 `norm_*` 比對鍵，[#689](#pr-689)）。這一種跑的不是 no-op 重抽，
  而是一個**真的會改資料**的 step。

沒動索引、沒動 `indexed_data` 算法、沒動衍生欄位規則的部署，**不需要**跑 migrate。

### 為什麼「光升版」不夠——兩層機制

migrate 是「把一列重跑一次目前的寫入路徑」，重跑時 `indexed_data` 會被**重新萃取**成最新形狀。它有一個保護：

> **migrate 會跳過任何「已經在最新 schema 版本」的列。**（specstar 在 route 層與 `ResourceManager.migrate`
> 各有一道 gate。）

所以要讓 migrate 真的動手，程式碼那邊必須先給那個 model 的 `Schema` **升一版**、加一個 step——
這是**開發者的事，已經隨版本發出**；運營方要做的只有把 model 名字丟給腳本。step 有兩種形狀：

| 形狀 | step 函式 | 資料 | 例子 |
| --- | --- | --- | --- |
| **重抽索引** | `_reindex_only`（identity） | 不變 | 大多數條目——純粹逼出「重新萃取 + 寫回」這個副作用 |
| **重算衍生欄位** | 真的回傳一個改過的 record | **會變** | [#293](#pr-293) 的 `token_count`、graph 的 `_renormalize_*` |

兩種都必須是純函式（只讀該列自己的欄位），所以**可重複執行**：已在最新版的列回 `skipped`；「重算衍生欄位」
型每次都從該列的原始欄位重算，重跑也安全。

> ⚠️ 開發者盤點「還有誰需要 migrate」時要對整個 `src/` grep `Schema(`——`workspace-file` 的在
> `filestore/specstar_impl.py`，各個 job model 的在自己的 coordinator 裡，不是只有 `resources/__init__.py`。

### 怎麼跑——`scripts/run_migrate.py`

腳本對每個 model 打它的 migrate route、串流進度、依狀態統計（`skipped` / `success` / `failed`），
最後印出回收空間要下的 SQL。

```bash
# 先 dry-run（走 migrate/test，串流一模一樣的進度但不寫回）
uv run python scripts/run_migrate.py --dry-run doc-chunk cluster-member

# 確認沒有 failed 之後正式跑（會重寫每一列的 meta，挑低流量時段）
uv run python scripts/run_migrate.py doc-chunk cluster-member

# 非預設主機、或有掛 root_path
uv run python scripts/run_migrate.py --base-url https://kb.example.com doc-chunk
```

route 掛在 `/api` 底下（`POST /api/{model}/migrate/execute`），身分沿用部署設定的 `server.default_user`，
不需要另外帶 token。任何一個 model 出現 `failed` 或連線失敗，腳本以 **exit code 1** 結束並列出是哪些列。
route 本身是 specstar 的 `MigrateRouteTemplate`，在 `make_spec` 全域註冊，每個 model 都有。

### 回填要打哪一顆 pod

migrate 只會把每一列帶到「**接到請求的那顆 pod** 認得的最新版」。滾動更新途中 Service 後面同時有新舊 pod，
打 Service 有機會落在舊 pod、回報成功卻沒有把新索引抽出來。一般 model 等 rollout 完再跑就沒這個問題；
唯一會**擋住 rollout** 的是 `workspace-file`（新 pod 在回填完成前不 ready，而 Service 只導給 ready 的 pod），
所以它必須 port-forward 直接打新 pod——步驟在 [#668](#pr-668)。

### 收尾：`REINDEX` 回收空間

migrate 把每一列重寫之後，**查詢速度會立刻恢復**，但**索引檔本身的體積**要等 `REINDEX` 才會縮回來——
舊的索引項會留成 dead entry。腳本會在成功後印出要下的指令：

```sql
REINDEX TABLE CONCURRENTLY doc_chunk_meta;
REINDEX TABLE CONCURRENTLY cluster_member_meta;
```

- 用 `REINDEX TABLE`（不指名索引）：按 table 名運作、對 specstar 的索引命名細節免疫，一次把該 meta table
  的所有索引重建乾淨。
- meta table 的名字是 `<table_prefix><model 的 snake 形>_meta`。預設沒有前綴，所以 `doc-chunk` →
  `doc_chunk_meta`；有設 `table_prefix` 的部署用 `--table-prefix` 讓腳本把它印進去。
- `CONCURRENTLY` 不鎖表，可以在服務運作中一個一個跑。

### 注意事項

- **挑低流量時段**：migrate 會重寫每一列的 meta。
- **順序**：先 dry-run，再正式跑，最後 `REINDEX`。
- **可重複執行**，重跑無害（上面說過）。
- **新增一次回填是開發者的責任**：幫該 model 的 `Schema` 加 step 升版，然後**在這份手冊加一條**。
  舊版文件的案例總表曾經停在 2026-07-20、之後兩批升版沒人補，而更早的八次 source-doc 升版根本沒上帳——現在規則寫在 CLAUDE.md：動到 `Schema` 版本、
  設定預設值、JobType、k8s manifests 的 PR，同一個 PR 就要帶條目。

## 附錄 B：不是資料遷移，但升版後容易被誤會成故障的事

**自動壓縮只有在解得出端點的 context 窗口時才會執行。** 解不出來時它完全不動，而畫面上和「壓縮壞了」
長得一樣。怎麼分辨、什麼時候才需要自己設 `history.context_limit`，寫在
[部署說明 §11——上下文窗口與自動壓縮](deployment.md#上下文窗口與自動壓縮誰決定怎麼確認什麼時候才需要你出手)
（規則只寫在那一邊，兩份會漂）。相關條目：[#632](#pr-632)、[#767](#pr-767)。

**legacy 切段器的中文修正（#806 P1）不需要重讀。** production 走 LlamaIndex 管線
（`kb_pipeline=get_doc_pipeline(...)`），中文本來就切得正常（實測 12,358 字 → 80 塊）；受影響的只有沒接
`kb_pipeline` 的 `create_app` 呼叫（測試、離線模式）。
