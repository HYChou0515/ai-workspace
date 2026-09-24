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

### 2026-09-18 · d94f6b77 · #811 WUI 總覽：`/wui` 列出 Deploy 過的頁面（`deployed-wui`） {#pr-811}

**設定** — 不動。沒有新旋鈕。

**資料**

- 新 specstar model **`deployed-wui`**（`api/wui_deploy.py`，post-`spec.apply` 註冊、沒有 auto-CRUD 路由）。
  **不用回填**：新表，上線時沒有任何列；一列只在有人按 Deploy 時寫入。**在這之前 Deploy 過的頁面不會
  出現在總覽，要到頁面上再按一次 Deploy**——這是設計（plan-wui-overview：Deploy 是「上架」的唯一一扇門，
  平台不掃 workspace 找 `view: wui`），不是遺漏。`icon` / `color` 兩個欄位（同一 PR 後半加的）都有預設值
  `""`，加欄位之前的列讀出來就是「沒有」、總覽畫預設圓圈和 App 的顏色；要用就在 view 檔加 `icon:` /
  `color:` 再 Deploy 一次。
- 我的最愛、卡片/表格的選擇都在瀏覽器的 `localStorage`（`rca.wuiFavourites`、`rca.wuiView`），
  不上伺服器：沒東西要遷、沒東西跨裝置。
- `GET /wui` 每一列多帶 `item_owner`（和 `item_title` 同一次讀取），前端靠它畫 owner 和「我的」篩選。

**行為**（⚠️ 不動設定行為就變）

- 工具列的 **Deploy** 多做第四步：build、確認頁面打得開之後，把這一頁**列上總覽**，四步都成才端出網址；
  上架失敗（最常見是沒有這個 item 的 `edit_content`）Deploy 就整個算失敗，不會端出網址。
- 導覽多一個入口 **WUI**（`/wui`），人人可見；列表按 item 的 `read_content` 過濾，**下架**只給有
  `edit_content` 的人。

**k8s · CI 側** — 不動。`sandbox-host/`、`kubernetes/` 沒改。

**確認做完**

- `GET /api/wui` 回 `{"pages": []}`（新部署）或已列的頁面；任一頁按 Deploy 後出現在裡面。
- `/wui` 開得出來、卡片模式預設；沒有 `edit_content` 的人看不到「下架」。
### 2026-09-18 · bc5c8cce · #815 SIGTERM 真的 graceful；app 聊天的 turn 換 pod 接手 {#pr-815}

**行為**（⚠️ 不動設定行為就變）

- 以前 uvicorn 在 SIGTERM 後**無限等**所有連線結束才跑 lifespan shutdown，而聊天／monitor 的 SSE 永不結束：
  只要收到信號時有人開著串流（rollout 幾乎一定有），turn drain 與 sandbox 拆除就跑不到、pod 在
  `terminationGracePeriodSeconds`（k8s 預設 30 秒）到期被 SIGKILL（本機探針 + uvicorn 原始碼推得；線上 log 沒看過）。
  現在 SIGTERM 當下 `/api/readyz` 回 503、所有 SSE 送 EOF（前端重連到活的 pod）、lifespan 在**一個 deadline**
  （`server.shutdown_budget_sec`，預設 20）內 drain：跑完的 turn 存檔；**app 聊天**跑不完的（在跑、排隊、還在準備
  的都算）先放掉認領再 cancel，**不存** partial、**不寫**「interrupted」，由別的 pod 乾淨重跑；**KB 聊天沒有認領**，
  超過預算的照舊 cancel、存 partial、寫標記。
- **app 聊天的 turn 換 pod 接手**：`ChatSendService` 每次 send 在存下問題的同時開一列耐久認領（新 model `turn-claim`，
  post-apply 註冊、無 CRUD 路由、**不需回填**；帶完整 send 配方，**不含**從 request 組出來的 env——重跑問
  `env_without_request`，`user_id` 是提問的人）。fleet 裡一顆 pod（`ScanLease`）每 `server.turn_reclaim_interval_sec`
  （預設 5 秒）掃孤兒認領，以對話為單位、整把拿或整把不拿：原 pod 放手的立刻接；心跳過期（30 秒）**且**那把 key 上
  沒有任何認領在 30 秒內剛開或剛被接（剛開、剛接的心跳還沒落地，不算孤兒）的整把接下、推一次 `TurnEpoch`、
  照順序重跑；重跑上限 2 次，再孤兒就寫一則 error 結尾（使用者看得到、可以重送）。兩個沒解、只明說的縫：
  兩顆 pod 的 tick 都先列表再各自 take、又在心跳過期兩側各讀一次，仍可能分著拿（一邊重跑被當 interrupted）；
  specstar 的 CAS 是 check-then-set，同一毫秒兩個 take 都會「贏」（後寫的擁有，另一份 `is_mine` 為 False）。
  認領就是帳本（回覆存進去才刪、不看對話尾巴猜）；代價：pod 恰好死在「回覆已存、認領還沒刪」那一毫秒時會多一個答案。
  升版當下在跑的 turn 沒有認領列，那一批仍是舊行為（pod 死就停在問題上）。
- shutdown 的 sandbox 拆除改用 `kill_idle` 的規則：先 write-back，整個 fleet 閒置超過 idle 門檻的才 kill，否則只丟掉
  本 pod 的 session（sandbox 是共用的，無條件 kill 會砍掉 peer 剛接手的那個）。`kind: local` 單機重啟後最近用過的
  item dir 會留在 scratch 上（跟以前 crash 一樣），碰到才被 idle reap。
- `read_image` / `read_page` / 畫圖審圖 / doc-question 回答的同步 LLM 呼叫從 event loop 搬到 thread——這是 liveness
  probe 失敗被殺的元凶。boot 失敗的 exit code 回到 3（`Server.run` 取代 `uvicorn.run` 後曾變 0）。

**設定**

- `server.shutdown_budget_sec`（新；預設 20；也是 uvicorn 的 `timeout_graceful_shutdown`）、
  `server.turn_reclaim_interval_sec`（新；預設 5；`0` = 關）。都有預設，不設即生效。`run_consumers: false` 不影響
  （sweeper 產出的是 turn，只有 API pod 能跑，留在 API）。

**k8s · CI 側**

- **rollout 前（跟新映像同一次 apply）**：`rca-app` 加 `terminationGracePeriodSeconds: 90`（≥ uvicorn 等待 ≤ budget + lifespan drain ≤ budget + 每個還有
  turn 的 engine 各 4 秒 + `preStop` 5 秒 + 拆除；預設值 20 + 28 + 5 = 53 秒進拆除）與
  `lifecycle.preStop.exec: sleep 5`（pod 刪除時 k8s 同時拿掉 endpoint 與送 SIGTERM，sleep 讓移除先傳開）。
  漏加的症狀：跟以前一樣——rollout 時 30 秒到期 SIGKILL，在跑的 turn 沒交接、對話停在問題上。
  範例見 `kubernetes/base/deployment.yaml`。

**確認做完**

- **rollout 後**：看任一 API pod 的結束 log（`kubectl logs <api-pod> --previous | grep -E 'drain: begun|shutdown complete'`）：`drain: begun` → `lifespan: shutdown complete` 在 budget 內出現（這些是 INFO
  行，app 沒設 root logger 時只有 WARNING 以上會印——本機可用 `scripts/check_sigterm_drain.sh` 驗，它自己包了
  `basicConfig`）。另外 `terminationGracePeriodSeconds` 已生效：`kubectl get pod <api-pod> -o jsonpath='{.spec.terminationGracePeriodSeconds}'` 回 `90`。

---

### 2026-09-18 · #818 skill hub：使用者之間分享 skill，不經過 dev 的版本庫（`skill-hub-entry`） {#pr-818}

**設定** — 不動。沒有新旋鈕。

**資料**

- 新 specstar model **`skill-hub-entry`**（`apps/skill_hub.py`，post-`spec.apply` 註冊、**沒有 auto-CRUD 路由**：
  寫入只走會先審查的 `publish_skill`）。`owner` / `name` / `forked_from` 有索引。**不用回填**：新表，上線時
  沒有任何列。檔案是 FileStore 裡 `skill-hub:<entry id>:<version>` 命名空間下的 blob，一個發布版本一個命名空間；
  列只在新版本寫完後才指過去，舊版本的命名空間在那之後才清——中途失敗留下的是沒人指的孤兒 blob，不是指向空的列。
- `SkillOrigin`（副本的 `.origin` manifest）多了 `entry` 欄位，預設 `""`；既有的 package skill 副本照舊解碼，不用動。
  ⚠️ **回滾**（`回滾前`）：`source: "hub"` 的 `.origin` 有**兩種**來源——從 skill hub **裝過**的副本，以及
  **發布過**的那個資料夾（發布方自己寫的、或本來就是 hub 副本的 `.skill/<name>/`：`publish_skill` 發完會把它的
  `.origin` 改寫成指向剛發的條目，讓面板不會對它自己提「有新版」；本來是 package skill 副本的資料夾**不改**，
  發布不動它在這個 item 裡的來源與預設開關）——舊碼的 enum 都不認得。漏做的症狀：**回滾後那個 item 的
  `GET …/skills` 與 Refresh 會 500**。package skill 的副本不受影響。要回滾就先把那些 `.skill/<name>/.origin`
  刪掉（副本本身照常可用，只是不再知道上游；發布方的資料夾則變回一般 workspace skill）。

**行為**（⚠️ 不動設定行為就變）

- 出貨的四個 app（`rca` / `pm` / `playground` / `topic-hub`）在 `agent.tools` 加了 `publish_skill` / `install_skill` /
  `search_skill_hub`，在 `agent.skills` 加了 `skill-hub`（`pm/default` profile 也列入）。**部署方自己的 app 若有
  `save_skill`，比照加**（`rollout 前`，改 fork 裡的 `app.json` / profile 再 build；為什麼：tool 的授權表
  是 App 的 ceiling，沒列就不會建給 agent；漏做的症狀：Skills 面板兩顆按鈕都在，「從 skill hub 裝」走 route
  照常能用，「發布」放進對話框的那句話會讓 agent 回答它沒有 `publish_skill`）。`TOOL_VERBS`：`publish_skill` /
  `install_skill` 吃 `edit_content`（和 `save_skill` 同）；自訂 preset 的 ceiling 照推。`publish_skill` 對
  sub-agent 是禁的（`SUBAGENT_FORBIDDEN_TOOLS`）。
- Skills 面板的 `GET …/skills` 多了 `upstream` 欄位（`live` / `unpublished` / `deleted` / `null`）；既有 package
  副本讀 `live`（上游從 package 移除時 `deleted`，`update_available` 照舊 `false`）。舊 API pod 沒帶這欄時前端
  照今天的行為。
- AI 審查走發布那個 turn 的 runner，**等不到就不上架**（tool 回錯誤、對話窗看到「審查服務無法連線」），沒有
  「未審」狀態。429 的等待走 turn 既有的規則：preset 有 `fallbacks` 時是 `failover.rate_limit_budget_s`
  （preset 可覆寫、預設 7200 秒）；單一 endpoint 時是 runner 自己迴圈的 120 秒（`LitellmAgentRunner` 的
  `rate_limit_budget_s`，`get_runner` 目前**沒有**從設定帶入）——發布那個 turn 可能因此等很久，這是刻意的。
  一個條目上限 20 MiB（`SKILL_HUB_MAX_BYTES`），發布前先量大小、超過就不讀。
- 前端多了 `/skill-hub` 與 `/skill-hub/:id` 兩頁、導覽多一個入口「Skill hub」。

**k8s · CI 側** — 不動。`sandbox-host/`、`kubernetes/` 沒改。

**確認做完**

- `GET /api/skill-hub/entries` 回 `{"entries": []}`（新部署）；在任一 item 的 Skills 面板按「發布到 skill hub」、
  送出那句話後，對話窗看到審查結果，`/skill-hub` 列出那一條。
- 另一個 item 的 Skills 面板「從 skill hub 裝」看得到它、裝進去後那一列有「可在此編輯」、下一輪 `read_skill` 讀得到。
- 部署方自己的 app：`agent.tools` 有那三個、`agent.skills` 有 `skill-hub`，否則 agent 會說沒有 `publish_skill`。

### 2026-09-19 · #827 onboarding 的文字改成 markdown、App 可以放圖（`GET /apps/{slug}/assets/{name}`） {#pr-827}

**設定** — 不動。沒有新旋鈕。

**資料** — 不動。沒有新 model、沒有索引、沒有 Schema 版本變動；`app.json` 的 `onboarding` 多一個選填 `footer`
（預設 `""`），舊的 `app.json` 一字不改照常載入。

**行為**（⚠️ 不動設定行為就變）

- 歡迎卡（`OnboardingModal`：Launcher 的平台層 + 每個 App 的 dashboard）的 `intro`、每個 point 的 `body`
  與新的 `footer` 現在當 **markdown**（GFM）渲染，不再是純文字；point 的 `title` 仍是純文字。內建五個 App 的
  文字掃過：只有 `pm` 一處反引號（`` `issues/N.md` ``）會變成行內程式碼，正是作者原意；沒有別的變化。
  **部署方自己的 App**（fork 裡的 `app.json`）若 onboarding 文字含 `*` `_` `#` `[` `` ` `` `$`，畫面會變排版
  （`rollout 前` 掃一次：`grep -n '"body"\|"intro"\|"footer"' apps/<slug>/app.json`；為什麼：純文字是合法
  markdown，但這幾個字元在 markdown 裡有意思——`$` 是因為這條管線含數學（`$5 and $10` 之間會被畫成公式）；
  `<` 不在清單裡：沒有 raw HTML，`<b>` 就照字面顯示（唯一例外是 GFM 的 `<https://…>` 自動連結，正是作者要的）；漏做的症狀：歡迎卡裡出現粗體／標題／連結／公式不是作者要的）。
- `rca` 的 onboarding **版本號 1 → 2**（第一個 point 多了建立表單的截圖）：每個使用者會**再看到一次** RCA 的
  歡迎卡，按「永遠不顯示」後就不再出現——這是 #161 定義的語意（教學內容變了就重新顯示），不是 bug。
- 新的唯讀路由 `GET /apps/{slug}/assets/{name}`：吐 App 目錄下 `assets/<name>` 的圖（副檔名白名單同 icon：
  png / svg / jpg / jpeg / webp / gif；純檔名；其他一律 404），**沒有額外授權**——跟 `/apps/{slug}` 同級，
  圖是 App 的公開說明。fork 裡的 App 要放圖就放 `apps/<slug>/assets/`，markdown 寫 `![](assets/<name>)`；
  寫法見 `docs/adding-an-app.md` 的 `onboarding` 欄位。

**k8s · CI 側** — 不動。`sandbox-host/`、`kubernetes/` 沒改；映像照常把 `assets/` 一起包（hatch 包整個
`src/workspace_app`，跟 profile 的 `*.tpl` 一樣）。

**確認做完**

- `GET /api/apps/rca/assets/create-investigation.png` 回 `200` + `image/png`；`GET /api/apps/rca` 的
  `onboarding.version` 是 `"2"`、`onboarding.footer` 存在（`""`）。
- 開 RCA 的 dashboard：歡迎卡第一個 point 底下有「Start an investigation」表單的截圖；390px 寬也是滿版一張、
  三個 point 都在（卡片會捲）。
- 部署方自己的 App：歡迎卡的字沒有多出粗體／標題／連結。

---
### 2026-09-19 · #824 author-skill 存檔前先照 writing-for-agents 整理一遍（`references/` 隨 skill 出貨） {#pr-824}

**設定** — 不動。沒有新旋鈕。

**資料** — 不動。沒有新 model、沒有回填。

**行為**（⚠️ 不動設定行為就變）

- 內建的 `author-skill` 從此**帶著檔案**出貨（`references/writing-for-agents.md`，Matt Pocock 的規則，MIT）。
  依 #589 的機制，帶檔案的 skill 在每個 workspace **第一次** `read_skill` / Apply 時會被複製成該 workspace 自己的
  `.skill/author-skill/`（之前它只有 `SKILL.md`，所以從不複製、永遠讀出貨版）。從那一刻起，dev 之後改出貨的
  `sample-skills/author-skill/SKILL.md`，**既有的 workspace 不會自動跟上**——要在那個 item 的 Skills 面板按
  「更新為出貨版本」（`rollout 後`，不用一次做完；只在你希望某個既有 item 拿到新版 guide 時逐 item 按）。
  漏做的症狀：部署了新版 guide，某些 item 的 agent 還照舊步驟走——不是沒部署到，是那個 item 有自己的副本。
  新 item、以及部署前從沒在該 item 用過 `author-skill` 的 item，第一次讀到的就是新版，不用動。
- `author-skill` 本身的步驟從六步變七步：草擬前先讀那份規則、審閱後多一步「整理」（只改措辭不改內容、告訴使用者
  收緊了什麼）、skill 的 `name` 改成在第一步就以 kebab-case 定下（因為內文指向 `.skill/<name>/references/…` 要在
  存檔前就寫得出來）。agent 幫使用者做 skill 時會多一輪對話與一次 `read_file`；沒有旋鈕可關。

**k8s · CI 側** — 不動。`sandbox-host/`、`kubernetes/` 沒改。

**確認做完**

- 在任一 item 跟助理說「幫我做一個 skill」：對話裡看得到它 `read_file` 讀 `.skill/author-skill/references/writing-for-agents.md`，
  草稿給你看之後、存檔之前多一段「整理」。
- 那個 item 的 Skills 面板裡 `author-skill` 這一列從此是副本（多了「還原成出貨版本」）；改了出貨 guide 之後那一列才會出現
  「更新為出貨版本」，按了才換新。

---
### 2026-09-19 · #823 從聊天視窗匯出文字（json / md）與影片：`chat-video` JobType、自己的映像 {#pr-823}

**行為**（不動設定也會多出來的東西）

- chat header 的 Export 開一個對話框：文字 JSON（照舊）、文字 Markdown（`?format=md`）、影片；三者都可以選訊息範圍
  （`?start=&end=`，絕對位置、半開）。影片排一個 `ChatVideoJob`，**寫進 item 的 workspace** `/exports/chat-video/…`
  （算 workspace 額度），旁邊的 `<輸出檔>.progress.json` 是進度也是取消把手（刪掉 = 取消，worker 最慢十來秒停：心跳 10 秒 + 錄影切片 2 秒），`<輸出檔>.chat.json`
  是那次的輸入（留著，改了再送就重生）。也能直接打 `POST /a/{slug}/items/{item_id}/chat-video`
  `{transcript, options, output_path?}` 用手寫的 transcript 生影片；gate 是 `read_content` + `add_content`。

**設定**

- 新段 `chat_video:`（`max_pixels` 1920×1080、`max_seconds` 180、`max_output_bytes` 100 MB、`heartbeat_seconds` 10、
  `stale_after_seconds` 60）。都有預設，不設即生效；表單只給上限內的值、伺服端照這段擋（422 點名哪個上限）。五個值都要正整數（留空 = null 也不行）、
  `stale_after_seconds` 要大於 `heartbeat_seconds`，否則開不了機（0 秒的心跳是每秒上萬次寫檔；不比心跳長的規則把在做的當死掉）。
  `max_output_bytes` 也是素材預讀的上限（請求的素材預算超過它會被夾到它，不是拒絕）。
  **改大 `max_pixels` 要重量 worker 的記憶體**（[chat-video.md 要多少資源](chat-video.md#要多少資源量的41-秒的範例1080p)）。
- `server.run_consumers: true`（單機 all-in-one）時 API 進程自己吃 `chat-video` job：那台要裝 `uv sync --extra chat-video`
  + `playwright install --with-deps chromium` + `ffmpeg` + `fonts-noto-cjk`；沒裝的話 job 失敗、進度檔寫那句安裝提示，API 不受影響。

**資料** — 不動。新 model `chat-video-job`（job 列，索引 `status` / `partition_key`）是新表，上線時沒有列，不用回填。

**k8s · CI 側**（`run_consumers: false` 的部署）

- **rollout 前**：多一種 JobType `chat-video` → `rca-worker-chat-video` Deployment（`python -m workspace_app.worker chat-video`），
  **用自己的映像 `rca-app-chat-video`**（`docker/Dockerfile` 的 `chat-video` stage：app + `chat-video` extra + Chromium + ffmpeg +
  `fonts-noto-cjk`；多出來的是 Chromium 與它的共用函式庫 + ffmpeg，那一層 **+1.63 GB**（#834 量的）。不塞進 `rca-app`，每顆 API pod 沒理由多這些；
  **build 一定帶 `--target chat-video`**，不帶 target 做出來的是 API image——見 [#834](#pr-834)）。這個 worker 和 blob-gc 一樣是**從 API 自己那整套組的**
  （`build_app`，只組不 serve）——它要寫 workspace，所以要掛 `data` 與 `scratch` 兩個磁碟區、用同一個 configMap，能連到
  sandbox-host（`kind: http`）。記憶體照量到的給：request 1 Gi / limit 2 Gi（轉檔峰值 gif 640 MB、mp4 320 MB；
  `workers.yaml` 的註解有數字）。`terminationGracePeriodSeconds: 900`：SIGTERM 進來時在做的那支會做完才退，最壞是它自己的三個期限相加
  （錄影 `預估 × 1.5 + 30 s` = 300 s、ffmpeg 每段 300 s、gif 兩段）；排隊的比 grace 還長的話會被 SIGKILL，那支由 queue 重送——
  但要等 specstar 的 stale sweep（每 60 秒一次、15 秒沒心跳算死）先把它的列標掉才會真的交給下一顆 worker，從頭再做（同一個 token）；
  最多重做 3 次，之後列 FAILED、進度檔留著、膠囊一直說「worker 沒有回應」直到人刪掉它或下一次請求取代它。CI 要多 build / push 這個映像。
  漏加的症狀：前端按了「開始做影片」後進度膠囊停在「影片排隊中 0 / 約 N 秒」，`stale_after_seconds`（60 秒）後多一句
  「排隊 N 秒，還沒有 worker 接手」（排隊中的檔沒人改寫心跳，前端只說它知道的事；「worker 沒有回應」是錄到一半心跳停了才說）；
  job 永遠 pending；檔案樹裡的 `.progress.json` 的 `heartbeat_at` 停在排隊的那一刻。

**確認做完**

- `kubectl get deploy rca-worker-chat-video` 有 1 顆 ready；從任一 item 的 chat header 匯出 → 影片 → 開始做影片，
  進度列每 10 秒前進、幾十秒後 `/exports/chat-video/` 出現 `.mp4`。用 curl 送一份三則的手寫 transcript 也應出檔
  （[chat-video.md 從 API 出固定字句的影片](chat-video.md#從-api-出固定字句的影片)）。

---

### 2026-09-20 · a61886bb · #828 工具 modal 按套件折疊；整包授權在 picker 變成逐指令列 {#pr-828}

**行為**（沒有新設定；運營方不用做事，但要知道以下幾件事）

- item 的「助理可用的工具」modal 改成按套件折疊，而且 **app 授整包的套件（`tools[]` 寫 `rca-tools` 這種）現在一個指令
  一列**，每列可各自預設／開啟／關閉；agent 拿到的工具集也改成逐指令算——config 在「packages 齊了」的那一點定案
  （`apps/catalog.py:finalize_tool_grants`），chat／workflow／排程 turn、WUI 頁面的 `callTool`、picker、replay 讀的是同一份答案。
  `app.json` 不用改。
- 既有 item 存的整包鍵（`attached_tool_prefs` 裡的 `"rca-tools": false`）**繼續有效**：讀的時候當成該套件每個指令都釘成那個值
  （指令鍵優先）。使用者在 modal 第一次（有改動的）儲存時，那把整包鍵會被拆成逐指令鍵寫回——資料在 item 列上，沒有 migrate 要跑。
  漏知道的症狀：無。沒有指令的套件（`python-stack`，它是 sandbox 的 Python 載體）仍是一列。
- 隨之而來、沒有開關的幾個語意（都是「picker 關掉的指令就是關掉」）：workflow step 的 `tools:` 寫 `rca-tools` 時拿到的是
  **這個 item 持有的那幾個指令**（被釘掉的不在），寫 `rca-tools:spc` 這種指令名也可以（驗證器認得部署有的套件指令：拼錯的指令名會被拒；解不出的套件或沒有指令的套件寫 `pkg:x` 驗證器擋不了，執行時丟掉並在 log 留一行 `workflow node: tools not held by this item, dropped`）；sub-agent 定義的 `tools`
  可以寫整包名或指令名（`save_subagent` 接受、載入與委派時縮成持有的指令；它拒絕時的「Available」清單從此列的是指令名）；
  WUI 頁面叫一個被釘掉的指令會 403（訊息指向 tool picker）。一個整包鍵現在也管到 app 只授部分指令的套件（以前那種鍵是 no-op）。

**確認做完**：開任一 rca item 的工具 modal，`Rca Tools` 是一個可展開的組（10 列）；`GET /api/a/rca/items/<id>/tools`
的 `rca-tools:*` 列 `group` 都是 `rca-tools`。

---

### 2026-09-21 · #834 docker：不帶 `--target` 的 build 又回到 API image；`chat-video` 層量到 +1.63 GB {#pr-834}

**設定** — 不動。**資料** — 不動。

**k8s · CI 側**

- Docker 不帶 `--target` 就 build 檔案裡**最後一個** stage。#823 把 `chat-video` stage 放在最後，所以 #823 之後照文件的
  `docker build -t rca-app … -f docker/Dockerfile .` 做出來的 `rca-app` 其實是 worker image：多 Chromium + ffmpeg（+1.63 GB）、
  `CMD` 是 `python -m workspace_app.worker chat-video`——而 `kubernetes/base/deployment.yaml` 的 API 容器**沒有**自己的 `command`，
  所以用那顆 image 起的「API pod」跑的是 worker 進程，不 serve HTTP：`/api/readyz` 永遠不 ready、rollout 卡住、舊 pod 繼續撐著。
  這版在最後補一個 `api` stage 把預設拉回 API。**`rollout 前`**：如果你們 CI 在 #823 之後 build 過 `rca-app`，用這版**重 build 一次**
  就瘦回去；worker image 一律 `--target chat-video`（`docs/deployment.md` §11 兩條指令）。
  漏做的症狀：新的 API pod 永遠不 ready，`kubectl logs` 裡是 worker 在消費 job、沒有 uvicorn；image 3 GB 級；
  `docker image inspect rca-app --format '{{.Config.Cmd}}'` 印的是 worker。

**確認做完**

- `docker image inspect rca-app:<tag> --format '{{.Config.Cmd}}'` 是 `[python -m workspace_app]`（不是 `workspace_app.worker chat-video`）；
  `docker run --rm rca-app:<tag> sh -c 'which ffmpeg; ls /ms-playwright'` 兩個都空。
- `docker run --rm rca-app-chat-video:<tag> sh -c 'which ffmpeg; ls /ms-playwright'` 有 `/usr/bin/ffmpeg` 與 `chromium-*`。

---

### 2026-09-22 · 86890da3 · #840 `server.run_consumers` 可以填清單；`${RUN_CONSUMERS}` 給的字串從此會被正確解析 {#pr-840}

**設定**（純 opt-in 的部分不用動；但要知道一個**行為改變**）

- 新形狀：`server.run_consumers: [index, card-gen, …]` = 只消費列出的 JobType（單機全包但跳過 `chat-video` 這種）。
  `true` / `false` 照舊。名字對 worker CLI 那張表驗證，拼錯**開機就拒絕**。不改設定的部署，行為不變——**除了下面兩條**。
- **行為改變、沒有開關**：以前 loader 不做型別轉換，`run_consumers: ${RUN_CONSUMERS}` 配 configmap 的 `RUN_CONSUMERS: "false"`
  到手的是**字串** `'false'`（truthy），所以用 `${RUN_CONSUMERS}` 接線的 API pod **一直在消費所有 JobType**，
  不是文件說的純 producer。這版起 `"false"` 就是 `false`。**`rollout 前`確認 worker Deployment 真的在跑**
  （base 的 `workers.yaml` 每種 JobType 一個；`kubectl get deploy | grep rca-worker-`）：worker 是冪等的 durable-queue 消費者，
  和舊 API pod 並存是安全的，先起再滾。如果你們的 `config.yaml` 寫的是字面 `false`，這條對你們沒有影響。
  漏做的症狀（哪種 job 沒 worker 就出哪種）：help 文件停在 `indexing`（`index`）、wiki 不再更新（`wiki`）、
  上傳的封存包一直 `pending`（`kb-import`）、blob GC 不再跑（`blob-gc`）、聊天影片匯出停在排隊（`chat-video`）。
  新版 API 的 stdout 會有一行 `⚠ consumers: NOT consumed on this process: …` 點名沒人消費的 JobType。
- **從「靜默接受」變「拒絕開機」**：YAML 的 `run_consumers:`（空值 / `null`）、`0` / `1`、`""`、mapping，和 `${RUN_CONSUMERS}`
  給的 `no` / `0` / `1` / `,` 以前都被吞掉（`null`、`0`、`""` 是 falsy → 純 producer；`1` 和 mapping → 全消費；env 給的
  `no` / `0` / `1` / `,` 是非空字串 → truthy → 全消費），這版起開機拒絕。`rollout 前` 看一眼你們的 `config.yaml` 這個 key
  是不是上面三種形狀之一（YAML 的 `yes` / `no` / `on` / `off` 是 PyYAML 布林，照常算 true / false）；漏做的症狀：新 pod
  CrashLoop，log 裡沒有 `config:` 那行也沒有 config dump（load 在它們之前跑；前面只有 nltk / LiteLLM 的 import 雜訊），
  最後一行是 `ValueError: server.run_consumers: …`。

**資料** — 不動。**k8s · CI 側** — manifest 沒改；configmap 的註解補了清單寫法，並把示範改成 block form
（原本的 `server: { run_consumers: ${RUN_CONSUMERS} }` 是 YAML parse error，照抄的 pod 從來起不來）。

**確認做完**

- 純 producer：`kubectl logs deploy/rca-app | grep 'run_consumers:'` 印的是 `run_consumers: false  # ← env`（舊版是帶引號的
  `run_consumers: "false"  # ← env`——引號就是字串沒被解析的證據），且 log 裡沒有任何 `→ start … consumer …` 這種 boot step。
  worker 那邊：`kubectl get deploy | grep rca-worker-` 列出你要的每一種（base 的 `workers.yaml` 九種都有）。
- 單機清單寫法：stdout 有 `→ start index consumer …`（你列的每一種一步）和一行 `⚠ consumers: NOT consumed on this process: …`
  （你沒列的那幾種，排序）。

### 2026-09-25 · #854 runtime view plugin 平台；`csv-table` 搬出 SPA、改由 plugin 目錄提供 {#pr-854}

**設定** — 不用動。新增選用的 `view_plugins.dir`（空 ⇒ `$WORKSPACE_VIEW_PLUGINS_DIR` ⇒ `<repo>/.view-plugins`，
映像裡是 `/app/.view-plugins`）。目錄不存在 = 沒有 plugin；**目錄裡任何一個 plugin 壞掉就拒絕開機**，
log 最後一行是 traceback 的 `workspace_app.view_plugins.discovery.ViewPluginError: …`，
後面點名是哪個 plugin 或哪個 kind（stdout 那行 `✗ discover view plugins (failed after …s): ViewPluginError` 不帶原因）。點開頭的目錄與 `lost+found` 會被略過。

**資料** — 不動。

**k8s · CI 側**

- **映像要經過新的 `view-plugins` stage 建出來**（`rollout 前`，build 時）。`docker/Dockerfile` 不帶 `--target`
  的 build 已經包含它（它排在 `app` 之前，`app` 把產物 `COPY` 到 `/app/.view-plugins`）；**自己寫 Dockerfile
  或自訂 build 流程的**要照做：`node view-plugins/build-web.mjs view-plugins/<name> <目錄>`（需要 node 與 pnpm），
  再跑 `python -m workspace_app.view_plugin check`。
  為什麼：`csv-table` 不再編進 SPA bundle，而是 SPA 開機時從 `GET /api/view-plugins` 載入。
  漏做的症狀：每個 `view: csv-table` 的面板顯示 `Unsupported view kind: csv-table`，其他畫面正常。
- **自己掛 plugin 目錄的**（設了 `view_plugins.dir` 或 `WORKSPACE_VIEW_PLUGINS_DIR` 指到 volume）要把
  `csv-table` 也建進去（`rollout 前`）：`uv run python -m workspace_app.view_plugin build view-plugins/csv-table <目錄>`。
  為什麼：映像裡的 `/app/.view-plugins` 只在沒改目錄時被讀到。漏做的症狀同上。
- **不走容器、`sandbox.kind: local` 直接跑 repo 的部署**：`rollout 前`跑 `make view-plugins`（寫到 `<repo>/.view-plugins`）。
  漏做的症狀同上。
- 反向代理 / CDN：SPA 的 `/shared/*.js`（import map 的目標，固定檔名）回 `Cache-Control: no-cache`；
  **不要**另外設規則把它們長期快取——重 build 之後舊的 `shared/*.js` 會配到新的雜湊 chunk，
  plugin 面板顯示 `view plugin "<名字>" is unavailable: …`。
- sandbox-host 這版**不用動**：本 PR 沒有任何 plugin 帶沙盒半邊。
- 這個 PR **無需動作**，只先講好規則（本 PR 的 plugin 都沒有沙盒半邊；#855 的 chart 會有）：`sandbox.kind: local`
  跑這個映像時，帶 `bundle` 沙盒半邊的 plugin **不會**擋開機——映像只裝 web 半邊，開機印
  `⚠ view plugin <名字>: sandbox.bundle … is not in this plugin dir…`，那個 plugin 需要沙盒的畫面逐次顯示錯誤
  （runner 502 說明兩種後端各自的修法）。為什麼不擋：預設部署就是 `kind: local`，擋了每個 API pod 都起不來。
  要它能算，在裝那種 plugin 的版本 `rollout 前` 掛一個用 `view_plugin build` 裝好的 plugin 目錄。
- **本機 / VM 的 `.workspace-tools` 快取會全部重建一次**（`uv run python scripts/prebuild_tools.py`，下次跑時自動發生）：
  工具包的建置標記納入了新的隔離 launcher 樣板。正式映像在 build 時就重建，沒有執行期成本；
  本機第一次會比平常久。

**確認做完**

- `kubectl logs deploy/rca-app | grep 'view plugins:'` 看到 `view plugins: csv-table ← /app/.view-plugins`
  （你改過目錄就是你的路徑）。
- 登入後 `GET /api/view-plugins` 回
  `[{"name": "csv-table", "sdk": "1", "kinds": ["csv-table"], "entry_url": "/view-plugins/csv-table/index.js"}]`。
- 在任一 item 開一個 `view: csv-table` + `source:` 指向 CSV 的 `*.ai.yaml`，畫出表格。

---

### 2026-09-25 · #855 chart view plugin：AI 用 `show_file` 秀出可互動圖表（`view: chart`） {#pr-855}

**設定**：沒有新 key。但有一個**行為改變，沒有開關**：預設映像帶 `chart` plugin。

- 凡是同時擁有 `write_file` 與 `show_file` 的 app，每一輪 prompt 都會多這些：
  - `## Available views` 整段，約 280 字元：標題、一句說明，加上 chart 的兩行。csv-table 沒有 views，
    所以這段是因為 chart 才出現；
  - skill 索引的 `chart` 一行，約 230 字元；
  - SKILL.md 本文約 5.0k 字元，只在 AI `read_skill('chart')` 時才載入。
- AI 主張資料關係時，會寫 `views/*.ai.yaml` 再 `show_file`。
- **要關掉它**：從 plugin 目錄（`view_plugins.dir`）移除 `chart`。只對某個 item 關掉，就在該 item 的
  skill 偏好把 `chart` 關掉。
- **為你們的模型重調**：`uv run python -m workspace_app.view_plugin tune chart`，改
  `<plugin 目錄>/chart/skill/SKILL.md` 再重跑。下一輪對話就生效。

細節見 [chart：互動圖表 view plugin](view-plugin-chart.md)。

**資料**：不動。沒有 `Schema` 升版。

**k8s · CI 側**

- **sandbox-host 映像要重 build，時機是 `rollout 前`**。
  - 做什麼：它的 tools stage 現在會把 `view-plugins/*/sandbox-src` 建進 `builtin/chart`。圖表的聚合，
    以及 `show_file` 的 `validate`，都在沙盒裡跑這個 bundle。
  - 順序：sandbox-host 先上、API 後上。
  - 漏做的症狀：
    - 打開任何 chart 檔，面板顯示 `view plugin "chart" could not run "query"`（502，沒有 `.tools/chart/launch`）；
    - AI 的 `show_file` 對 chart 檔一律回 `error: view plugin 'chart' refused …`，對話裡不出現卡片。
- API 映像的 plugin stage（`view_plugin build`，前端 `index.js` + skill）見 #854 的條目。這個 PR 只是在
  `view-plugins/` 多放一個 plugin。

**確認做完**

- 在 sandbox-host pod 裡：`/opt/tools/builtin/chart/launch` 沒帶參數，印出含 `validate` 與 `query` 的 JSON 清單。
- `GET /api/view-plugins` 列出 `chart`。
- 在一個 workspace 放一份 CSV，叫 AI「用圖表說明 X 和 Y 的關係」：
  - 回覆出現一行 `… rows; <欄位> <最小>–<最大>` 摘要與一張卡片；
  - 點開卡片是可以框選的圖表；
  - 有 `highlight:` 時，被點亮的點保持原色，其餘變淡。

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
