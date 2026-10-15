# 容量規劃（capacity）

這一頁回答兩個問題，給 superuser 看（`/capacity`，其他人 403）：

1. **我們吃掉多少資源？**每一種資源（CPU、記憶體、磁碟、GPU），每個人在每個地方（API、背景工作、沙盒、
   資料庫、GPU）用了多少。
2. **人更多時需要多少？**N 個人要幾台機器、幾張 GPU，哪一項最先不夠，照現在的成長什麼時候要下單。

設計與取捨在 [plan-capacity-usage.md](plan-capacity-usage.md)。這份文件寫運營方要做的事：**平台寫出用量紀錄，
你們的收集器送進 ES；四個資料來源由你們實作，平台只定介面**。

## 1. 平台寫出什麼

每筆紀錄是 stdout 上一行 JSON（設定 `capacity.sink` 可以換成別的寫法）：

```json
{"@timestamp":"2026-10-15T04:05:00.000Z","event":{"dataset":"workspace.usage"},
 "usage":{"v":1,"kind":"work","pod":"api-7f9c","component":"api","user":"alice","app":"pm",
          "work":"req:GET /api/a/{slug}/items/{item_id}/files","count":12,"seconds":0.84,"h1":9,"h2":3,"window_s":300}}
```

- **收集器只要做一件事**：`event.dataset == "workspace.usage"` 的行送到自己的 index（例如 `workspace-usage-*`）。
- `@timestamp` 是這筆數字涵蓋的時段的**開頭**（`work`、`sandbox`、`sandbox_host`），或事件開始的時間
  （`turn` 是訊息被接受的時間、`job` 是開始執行的時間）。時段對齊到 epoch 的整數倍，所以 5 分鐘的
  date histogram 每個時段只會落在一個桶。
- 有時段的種類裡，**量到的數字都可以直接加總**（次數、秒數、記憶體×秒）。例外是描述而不是量的欄位：
  `sandbox.mem_max` 取最大值；`cpu_limit`、`mem_limit`、`window_s` 是設定，不加總。

| `kind` | 誰寫 | 什麼時候 | 欄位 |
|---|---|---|---|
| `work` | API、worker | 每個時段，每個行程裡每人 × App × 工作種類一筆 | `user` `app` `work` `count` `seconds` `h0`…`h9`（只有請求有，0 不寫）`window_s` |
| `turn` | API | 每個對話回合結束 | `user` `app` `item` `surface` `outcome` `accepted_ms` `started_ms` `first_event_ms` `first_output_ms` `ended_ms` `model` `prompt_tokens` `completion_tokens` |
| `job` | 跑 job 的行程 | 每次 job 執行結束 | `user` `jobtype` `enqueued_ms` `started_ms` `ended_ms` `outcome` `attempt` |
| `sandbox` | sandbox-host（或 `kind: local` 隔離模式的 API） | 每個時段，每個沙盒一筆 | `item` `user` `app` `sandbox` `cpu_s` `mem_bs` `mem_max` `thr_s` `thr_n` `periods` `cpu_psi_s` `mem_psi_s` `oom` `cpu_limit` `mem_limit` `window_s` |
| `sandbox_host` | 同上 | 每個時段一筆 | `live_s`（活著的沙盒×秒）`cpu_psi_s` `mem_psi_s` `window_s` |
| `disk` | blob-gc worker 的 disk-ledger 那一輪 | 每輪對帳（`resources.disk_reconcile_interval_sec`，預設 6 小時） | `scope`（`workspace` / `kb`）`item` 或 `collection` `user` `app` `bytes` |
| `meta` | API、worker | 有紀錄因佇列滿被丟掉的時段 | `dropped` `window_s` |

`work` 的種類：`req:<METHOD> <route template>`（請求；`seconds` 是牆上時間）、`probe:<METHOD> <route template>`
（Kubernetes 的探針，記成 `system`；不算請求——見下段）、`stream:<route template>`
（SSE 長連線；`seconds` 是這個時段裡連著的秒數——開著的分頁沒有動作也會每個時段記一筆）、`turn:<surface>`
（回合在跑的秒數）、`turn-events:<surface>`（發出的事件數）、`job:<jobtype>`（job 在跑的秒數）。延遲桶
`h0`…`h9` 的上限是 50、100、250、500、1000、2500、5000、10000、30000 ms，以及更慢。

**這個請求是誰的**：路由自己問了 `get_user_id`，就用那個答案；`/api` 底下對到了路由、但路由沒問的（例如
自動 CRUD），回應之後平台再問一次 `get_user_id`；問不出來，或是靜態檔、沒有任何路由接的網址，就記成
`unknown`。沒有人觸發的工作記成 `system`，不問 `get_user_id`：沒有建立者（`created_by`）的 job，以及路由沒問
是誰的 Kubernetes 探針（`User-Agent: kube-probe/…`，每個 pod 每幾秒一次——誰都能寫這個 header，所以問過是誰的
路由照常記給那個人）。sweeper 排的 job 有建立者：是部署的 `get_user_id` 在請求之外回答的人（通常是
`server.default_user`），會被當成一個人算進 N。`unknown` 和 `system` 在畫面上是獨立的一列，占比都寫在「可信度」。
探針另外記成 `probe:` 而不是請求：每個 pod 每個時段的次數都一樣，當成一種請求的話，它跟 pod 的閒置成本一起
動、分不開，回歸會把整個閒置成本算到「系統工作」頭上；它很快的回應也會把請求的延遲 p95 拉低。它的 CPU 算在
基本開銷裡。

**App** 是對到的路由的 `slug`，三個條件都成立時才記：路由在 App 裡面（`/api/a/{slug}/…`；`/api/apps/{slug}`、它的
圖示和附圖都不算用了那個 App——首頁會載入每個 App 的圖示）、slug 是平台帶著的 App，而且回應沒有拒絕這個 slug
（不是 4xx）——網址上的 slug 在路由接受之前，只是呼叫者寫的字（有的路由對任何 slug 都回成功，資料庫出問題時
也會是 5xx）。做了但失敗的工作（5xx、回應前就當掉）仍然算那個 App 的。另一頁替使用者呼叫的 App 路由也算用了
那個 App：skill hub 的「安裝到 workspace…」逐一問每個 workspace 的安裝狀態（`/api/a/{slug}/items/{item_id}/skills/install-state`），
開過那個清單的人就會出現在那些 App 的使用者裡。

### ES 的 mapping

字串一律 `keyword`，數字一律 `double`，`*_ms` 用 `long`：

```json
PUT _index_template/workspace-usage
{
  "index_patterns": ["workspace-usage-*"],
  "template": {
    "mappings": {
      "properties": {
        "@timestamp": {"type": "date"},
        "event": {"properties": {"dataset": {"type": "keyword"}}}
      },
      "dynamic_templates": [
        {"ms": {"path_match": "usage.*_ms", "mapping": {"type": "long"}}},
        {"strings": {"path_match": "usage.*", "match_mapping_type": "string", "mapping": {"type": "keyword"}}},
        {"integers": {"path_match": "usage.*", "match_mapping_type": "long", "mapping": {"type": "double"}}},
        {"fractions": {"path_match": "usage.*", "match_mapping_type": "double", "mapping": {"type": "double"}}}
      ]
    }
  }
}
```

### 量有多大

每個時段的列數 ≈ 每個行程在那個時段裡「有在動的（人 × App × 工作種類）」數，加上每個活著的沙盒一列。
一個人的請求會散在幾個 API pod 上，每個 pod 各寫自己的那一列。舉例（每個數字都是假設）：同時 40 人、
每天 10 小時、每人每 5 分鐘碰 8 種路由、3 個 API pod，`work` 最多約 40 × 8 × 3 = 960 列／時段 × 120 個時段 =
每天 115,200 列；100 個沙盒開 10 小時再多 12,000 列。上線之後，「總覽」的資料來源清單會寫分析實際讀到幾列
（① 是彙總後的列，加上 turn／job 的原始紀錄數）。要更細的解析度，要一起降 `capacity.step_s`、`capacity.window_s` 和
`SANDBOX_HOST_USAGE_WINDOW_S`（例如都設 60；列數約多 5 倍）——解析度是 `step_s`，只降 `window_s` 沒有用。

## 2. sandbox-host 自己量沙盒

sandbox-host 每 `SANDBOX_HOST_USAGE_SAMPLE_S` 秒（預設 60；**設 `0` 就不取樣**）讀一次每個活著的沙盒的 cgroup
（`cpu.stat`、`memory.current`、`memory.events`、`cpu.pressure`、`memory.pressure`、`cpu.max`、`memory.max`），
每 `SANDBOX_HOST_USAGE_WINDOW_S` 秒（預設 300）寫一筆 `sandbox`，一樣是 stdout 的 JSON 行。窗口要整除 3600
（不整除的值，包括 0，起不來），而且要**整除 `capacity.step_s` 或是它的整數倍，90 天期間的時段（預設 900 秒）
也一樣**——預設 `step_s: 300` 下只有 300 的因數（例如 60、100、150、300）和 900、1800、3600 可以。放不進時段的窗口，分析沒辦法放到對的時段，`capacity check`
會報錯。sandbox-host 是另一個服務：`capacity.record: false`
停不了它，要停就設 `SANDBOX_HOST_USAGE_SAMPLE_S=0`。

沙盒屬於誰，是 app 在建立沙盒時告訴它的（`labels: {user, app}`，user 是 item 的擁有者，與額度扣在誰身上
同一條規則）；舊版 app 沒送的話，沙盒照量，但記成 `unknown`。`kind: local` 但沒有開隔離的部署，沙盒沒有
各自的 cgroup，量不到每個沙盒，畫面會說明；沙盒的用量算在 API pod 裡。

**節流（throttled）和壓力（pressure）回答的是不同問題**：節流是沙盒撞到**自己的** `cpu.max`（它的上限太低）；
壓力是它在等 CPU。核心把節流的時間也算進沙盒自己的壓力，所以「變慢的門檻」(d) 看的是**壓力減掉節流**——
等的是機器（要買機器）；節流另外畫成一組點（是上限太低）。核心沒開 PSI 的話，沙盒的紀錄沒有壓力欄位，
(d) 會寫「這個核心沒有提供 CPU 壓力（PSI），看不到沙盒在等機器」，`capacity check` 也會警告。

## 3. LLM 呼叫帶上標籤

平台的每次 LLM 呼叫（agent、KB 的 LLM、VLM、embedding）帶 `x-litellm-tags` header：

```
x-litellm-tags: ws,ws.user:alice,ws.app:pm,ws.purpose:chat,ws.call:agent
```

- `ws` 是這個平台的記號，讓 ④ 分得出平台的流量和其他系統的；值除了英數字與 `_.-~` 之外都會 percent-encode。
- LiteLLM 把它存在 spend log 的 `request_tags`。沒有 item 標籤：上千個不同的 item id 會灌進 LiteLLM 的每日
  標籤彙總表（每個標籤 × 日期 × key × 模型……一列）。
- 沒有建立者的 job 帶 `ws.user:system`（sweeper 排的 job 帶建立它的那個人）；不在任何工作範圍裡的呼叫
  （健康檢查之類）不帶標籤；沙盒裡的程式自己呼叫 LiteLLM 也不帶，記在它用的那把 key 上。
- ⚠️ **LiteLLM proxy 設了 `general_settings.reject_clientside_metadata_tags: true`**：帶標籤的請求一律被拒（400）。
  rollout 前查這個設定；有的話設 `capacity.llm_tags: false`。
- ⚠️ **LiteLLM 有開 tag routing（`router_settings.enable_tag_filtering`）**：一個請求的標籤對不到任何
  deployment 時，LiteLLM 會送到 `default` pool，沒有 default pool 就**拒絕**。rollout 前確認有 default pool，
  或設 `capacity.llm_tags: false`。
- ⚠️ **模型不是經由 LiteLLM、而是直接呼叫外部供應商**：標籤裡的使用者 id 會跟著送出去。這種部署設
  `capacity.llm_tags: false`。
- 標籤只在平台有在記錄時才送（`capacity.record: true`）。

## 4. 四個資料來源（你們實作）

在 `config.yaml` 用 dotted path 指到你們的 class（零參數建構），沒指的就是沒接上，需要它的視圖會說缺什麼：

```yaml
capacity:
  usage_records: acme_capacity.sources.EsRecords      # ①
  resource_metrics: acme_capacity.sources.PromPods    # ②
  gpu_metrics: acme_capacity.sources.DcgmGpus         # ③
  llm_requests: acme_capacity.sources.LiteLlmLog      # ④
```

介面在 `src/workspace_app/capacity/sources.py`。共通的規則：

- 每個方法是**同步**的，平台在 worker thread 裡呼叫，可以直接用同步的 ES／HTTP／DB client。
- 時間一律 epoch 毫秒；每個桶的 `t_ms` 是它的**開頭**，**對齊 `step_s * 1000` 的整數倍**。
- 寫好之後跑 `uv run python -m workspace_app.capacity check --hours 24`（見 §5）。
- 除了 API，**blob-gc 與 chat-video 兩種 worker 開機時也會建構這些 class**（它們組出 API 的整套 model 註冊）：
  class 建構時要的 env 或憑證，這兩個 worker 的 Deployment 也要有，否則它們起不來。

### ① 平台用量紀錄（`UsageRecordSource`）

兩個方法：

- `aggregate(q)`：在 `[q.start_ms, q.end_ms)`、`usage.kind == q.kind`、`usage.<欄位> == q.where` 的紀錄上，
  依 `q.group_by` 的欄位（缺的當 `""`）和 `q.step_s` 的時間桶（`None` 就是整段一桶，`t_ms` 為 `None`），
  對 `q.sums` 加總、對 `q.maxes` 取最大。同一個欄位不會同時在兩邊。**一個桶裡沒有任何紀錄帶某個 `maxes`
  欄位時，那個欄位不要出現在 `values` 裡（不是 0）**——平台靠這個分辨「核心沒有 PSI」與「沒有人在等」。
  **語意的參考實作**是 `sources.aggregate_documents`——同樣的事，在一串紀錄上做。
- `records(kind, start_ms, end_ms)`：那段時間所有 `kind`（`turn` 或 `job`）的 `usage` 物件。

ES 的寫法是一個 composite aggregation（翻頁直到沒有 `after_key`）：

```json
POST workspace-usage-*/_search
{
  "size": 0,
  "query": {"bool": {"filter": [
    {"term": {"usage.kind": "work"}},
    {"range": {"@timestamp": {"gte": 1760500000000, "lt": 1760586400000, "format": "epoch_millis"}}}
  ]}},
  "aggs": {"g": {
    "composite": {"size": 1000, "sources": [
      {"t": {"date_histogram": {"field": "@timestamp", "fixed_interval": "300s"}}},
      {"user": {"terms": {"field": "usage.user", "missing_bucket": true}}},
      {"work": {"terms": {"field": "usage.work", "missing_bucket": true}}}
    ]},
    "aggs": {"count": {"sum": {"field": "usage.count"}},
             "seconds": {"sum": {"field": "usage.seconds"}}}
  }}
}
```

`maxes` 用 `max` 子聚合；它在桶裡沒有值時回 `null`，那一個就不要放進 `values`。`records` 用
`sort: [{"@timestamp": "asc"}]` 加 `search_after` 把 `_source.usage` 全部取回。

### ② pod 與機器（`ResourceMetricsSource`）

- `usage(start_ms, end_ms, step_s)`：每個桶、每個 pod 一筆 `ResourceSample(t_ms, component, pod, cpu_cores,
  memory_bytes, disk_bytes)`——CPU 是這個桶的平均核數，記憶體是這個桶的平均位元組；volume（資料庫的、檔案儲存的、
  沙盒暫存的）給 `disk_bytes`。`component` 只能是 `api`、`worker`、`sandbox-host`、`postgres`、
  `filestore`、`scratch`。
- `capacity()`：每個 component 一筆 `CapacityEntry(component, cpu_cores, memory_bytes, disk_bytes, machines,
  machine_label)`——它總共能用多少、由幾台組成、多買一台是什麼（`"node 32 cores / 128 GiB"`）。
  背景工作是**每種 job type 一條線**（index、wiki、graph……各自的 Deployment 各自擴縮）：一個 worker pod 跑哪種
  job，是從它的 `work` 紀錄看出來的。要給某一種 job 自己的容量，加一筆 `component="worker:<jobtype>"`
  （例如 `worker:graph`）；沒有的話，共用 `worker` 那一筆：這種 job 現在的 pod 數占所有共用這一筆的 job 現在的
  pod 數的比例——加起來剛好是那一筆。「現在」是「任何 worker pod 回報的最後一個時段」和它前面兩個時段，取這三個
  時段 pod 數的中位數（一個 pod 在它漏掉的那一格前後都有樣本，那一格也算它在）：只差一個時段的——漏了一筆樣本、
  換版時新舊 pod 並存的那一格——不算；持續兩個時段以上的變化才算，縮到 0 的 job 分不到。這種線的「幾台」是它分到的比例 × 那一筆的 `machines`，可以是小數（畫面寫到小數一位）。

Prometheus 的寫法（`query_range`，`step=300s`）。**注意時間**：`rate(x[5m])` 在時間 t 算的是 (t−5m, t]，
也就是「結束在 t 的那個桶」，但這裡的桶是用開頭標時間的——所以 `start`、`end` 都加一個 step 去查，
回來的每個時間再減一個 step：

```promql
# CPU：平均核數
sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="ws", container!="", pod=~"workspace-api-.*"}[5m]))
# 記憶體：這個桶的平均
sum by (pod) (avg_over_time(container_memory_working_set_bytes{namespace="ws", container!="", pod=~"workspace-api-.*"}[5m]))
# volume 用量
sum by (persistentvolumeclaim) (kubelet_volume_stats_used_bytes{namespace="ws"})
```

容量可以從 `kube_node_status_allocatable` 加總那個 component 用的 node pool，或從 pod 的 limit × replicas 算。

### ③ GPU（`GpuMetricsSource`）

- `usage(...)`：每個桶、每張卡一筆 `GpuSample(t_ms, card, model, util, memory_bytes)`，`util` 是 0–1；
  `model` 是**這張卡服務的模型，用 ④ 用的同一個名字**（LiteLLM 的 `model_group`，不是 GPU 型號）。
- `cards()`：每張卡一筆 `GpuCard(card, node, model, memory_total_bytes)`。

DCGM exporter 的 `DCGM_FI_DEV_GPU_UTIL`（0–100，除以 100）與 `DCGM_FI_DEV_FB_USED`（MiB，乘 1024²）；
卡對到模型，用 exporter 帶的 pod 標籤對你們的模型部署。**GPU 使用率不是容量指標**：一個請求在解碼時就讓
它接近 100%（「一個以上 kernel 在跑的時間比例」），所以 GPU 的容量看的是 ④ 的「開始變慢時的同時請求數」，
③ 用來知道每個模型有幾張卡、記憶體用多少。

### ④ LiteLLM 的請求紀錄（`LlmRequestSource`）

- `usage(...)`：每個桶 × 模型 × **請求的整串標籤**一筆 `LlmUsageRow(t_ms, model, tags, requests,
  prompt_tokens, completion_tokens, busy_seconds)`；`busy_seconds` 是這個桶開始的請求的 (end − start) 總和。
  其他系統的請求也要給（它們的標籤沒有 `ws`）——GPU 是共用的。
- `model` 用 **LiteLLM 的 `model_group`**：請求到 proxy 時用的名字（`model_list` 的 `model_name`），不是背後
  deployment 的 `litellm_params.model`。對話回合記的是平台請求時寫的名字（例如 `openai/qwen3`），分析把兩邊當
  同一個模型的條件是：名字一樣，或一邊是另一邊前面多一段 `provider/`（`openai/qwen3` 對 `qwen3`）。對不上的話
  view 8 就沒有那個模型的「第一個輸出」配對；`capacity check` 會列出回合用了、但 LiteLLM 從沒記過的模型名。
- `latency(...)`：每個桶 × 模型一筆 `LlmLatencyRow(t_ms, model, requests, ttft_p50_s, ttft_p95_s,
  tokens_per_s_p50)`；首字時間是 start → 第一個 token，生成速度是 completion tokens ÷ (end − 第一個 token)。

讀 LiteLLM 的 Postgres（欄位名稱依你們的 LiteLLM 版本調整）：

```sql
-- usage
SELECT to_timestamp(floor(extract(epoch FROM "startTime") / 300) * 300) AS t,
       model_group AS model, request_tags,
       count(*) AS requests, sum(prompt_tokens), sum(completion_tokens),
       sum(extract(epoch FROM "endTime" - "startTime")) AS busy_seconds
FROM "LiteLLM_SpendLogs"
WHERE "startTime" >= to_timestamp(:start_ms / 1000.0) AND "startTime" < to_timestamp(:end_ms / 1000.0)
GROUP BY 1, 2, 3;

-- latency
SELECT to_timestamp(floor(extract(epoch FROM "startTime") / 300) * 300) AS t, model_group AS model, count(*),
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY extract(epoch FROM "completionStartTime" - "startTime")),
       percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM "completionStartTime" - "startTime")),
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY completion_tokens
           / nullif(extract(epoch FROM "endTime" - "completionStartTime"), 0))
FROM "LiteLLM_SpendLogs"
WHERE "startTime" >= to_timestamp(:start_ms / 1000.0) AND "startTime" < to_timestamp(:end_ms / 1000.0)
  AND "completionStartTime" IS NOT NULL
GROUP BY 1, 2;
```

`request_tags` 是 JSON 陣列，原樣轉成 tuple 傳回即可（平台自己解析 `ws.*`）。

## 5. 檢查你們的實作

```bash
uv run python -m workspace_app.capacity check --hours 24 [--config config.yaml]
```

對每個接上的來源呼叫每個方法，回報：

- **error**（exit 1）：任何來源的桶沒對齊（①②③④ 都查）、不認得的 component（樣本的，或容量項目的——
  容量項目可以是 component 或 `worker:<job 類型>`）、負數或不可能的值（一個 pod 用的 CPU 比它的 component
  總共還多、GPU 使用率不在 0–1）、`machines` 小於 1、沙盒紀錄的窗口放不進時段（或 90 天期間的時段）、
  turn／job 紀錄缺時間欄位、方法丟錯；
- **warning**：每個時段都該有樣本的來源（②③）出現連續 3 個時段（預設 15 分鐘）以上的缺口（列出共幾個、
  共幾小時，和最長的 5 個）、沒有 `work` 或沙盒紀錄、沙盒紀錄沒有 CPU 壓力（核心沒有 PSI）、有用量卻沒有
  容量的 component、不認得的工作種類、③ 的樣本寫了沒有任何一張卡服務的模型、LiteLLM 的列沒有這個平台的
  標籤或沒寫是誰、`ws.*` 標籤格式不對、對話回合用的模型名 LiteLLM 從沒記過；
- **info**：各來源讀到幾列、幾個人、幾筆沙盒列、幾筆 turn／job 紀錄、平台占 LiteLLM 忙碌時間的比例。

畫面的「資料來源」也列出每個來源接上沒、讀到幾列、最後的錯誤；讀取失敗的來源，用到它的視圖會寫出錯誤。

## 6. 數字怎麼來（給看畫面的人）

| 元件 × 資源 | 每人分到多少 |
|---|---|
| API、資料庫的 CPU；每種 job 的背景工作 CPU | 每種工作的平均成本 × 這個人的次數或秒數；成本是用很多個時段的「工作量」對上「pod 的 CPU」解出來的（utilization law + 非負最小平方）。工作量有請求、即時連線的秒數、回覆事件、job 的秒數 |
| API、每種 job 的背景工作記憶體 | 同上，對上的是同時開著的連線、在跑的回合與 job |
| 沙盒的 CPU、記憶體 | 直接量：每個沙盒的 cgroup |
| 磁碟 | 直接量：每個 item 的工作區、每個 collection 的文件 |
| GPU | 直接量：這個人的請求在處理中的秒數（LiteLLM 的標籤） |
| 資料庫的記憶體與磁碟、沙盒暫存、GPU 記憶體 | 不分到人（基本開銷）；有容量的磁碟（資料庫、沙盒暫存、檔案儲存）另外給「每多一個人每月多多少」 |

- **期間**：「最近 N 天」是今天以前 N 個完整的日子，加上今天到目前為止（依 `capacity.timezone` 的日界）。
- 「N 個人」是最近 30 天用過至少一次的人。**開始記錄的頭 30 天**，這個數只算得到記錄以來出現過的人，會偏少，
  試算也一樣——畫面會標明；時程的趨勢只用「30 天都已記錄」的日子，否則人數的累積會被當成成長。
- **試算**從過去的工作日抽樣（沒設工作日曆時是週一到週五），只抽 ①②④（有接上的）都有資料的日子——開始記錄之前的日子不是
  「沒人用」：抽一天，留下那天的真實的人，再從選的那一群人裡抽到 N 人，看那一天的尖峰；抽 400 次，取 p50 與
  p95。最多看到現在人數的 20 倍，問更多時畫面會寫算的是幾人。
- **還能撐幾人**：p95 的尖峰還在每一條會隨人數變的線的安全門檻（容量 × 門檻）以下的最多人數（不隨人數
  變的線——資料庫記憶體、GPU 記憶體——和磁碟不算在內，磁碟看時程）；看到 20 倍都還放得下時，寫
  「N 人以上」，時程也不給下單日。
- **時程**：最近 30 天用過的人數的直線趨勢，走到「還能撐幾人」的日子，附 80% 的預測範圍——看得越遠範圍越寬；
  人數的起伏比成長大時，範圍慢的那一端永遠到不了，畫面會說最晚說不準。下單日 = 範圍早的那一端 − 採購天數；
  已經過了的話，畫面說現在就該下單。
- **安全門檻**是「變慢的門檻」量到的轉折點（GPU 是容量）；還沒量到——或「變慢的門檻」還沒準備好（①② 都有資料的
  7 天）——時用預設值（CPU 70%、記憶體 80%、磁碟 80%），畫面會標明；GPU 那時沒有容量，沒接 ② 就一直沒有（用到它的
  總覽和試算本來就要 ②）。
- **回測**：用前面的日子預測最近 14 天每天的尖峰，平均誤差超過 20% 時，試算與時程標示「僅供參考」。每條拆分線
  另外寫「用之前的日子預測最後 7 天」的誤差。
- 每個視圖需要多少資料才會出現（期間放不下的，畫面會請你改看較長的期間；放得下的，資料夠了就自動出現）：

| 視圖 | 需要 | 多久之後 |
|---|---|---|
| 總覽 | ② | 1 天（數 ② 有資料的天）；「還能撐幾人」同試算——試算還不能算時，總覽也不給 |
| 分攤、個人明細 | ① | 1 天；拆分 API／背景工作／資料庫要 ① ② 都有資料的 3 天 |
| 每人用量分布 | ① | 7 天、至少 10 人 |
| 趨勢 | 任一來源 | 2 天 |
| 試算 | ①、② | 14 天（各來源都有資料的天）、至少 10 人，而且有用回歸拆分的線（② 回報了 API、背景工作或資料庫的 pod） |
| 時程 | ①、② | 28 個「30 天都已記錄」的日子——開始記錄後約 58 天；28 天的期間正好放得下 |
| 變慢的門檻 | ①、② | 7 天，負載變化至少 20 個百分點 |
| 可信度 | ①、② | 回測 21 天 |

分析在 API pod 上按需計算：在 thread 裡算，不卡事件迴圈；同一時間只算一個期間；結果在記憶體留 10 分鐘就放掉，
什麼都不寫；「重新計算」會丟掉它。90 天的期間用 15 分鐘以上的時段——格子越長，短暫的尖峰被平均得越多，所以 90
天的「還能撐幾人」會比 28 天的寬鬆一些；總覽的「依據」會寫出格子長度。讀到的列（含 30 天回看、turn／job 紀錄、
延遲列）超過 `capacity.max_rows` 時會停下來，畫面說「請改看較短的期間」。

**一個期間的分析佔 API pod 多少記憶體**（量自 60、120、240 人的模擬資料，來源每次回新的紀錄，像 ES client
那樣）：約 4 bytes × 時段數 × 每條線花到的人數（加總所有的線），再加每讀一列約 45–60 bytes（turn／job 紀錄只留
分析要的五個欄位，一筆約 30 bytes，也算在列數裡），再加每條線每個 App、每種用途 8 bytes × 時段數。時段數：7 天
約 2,304、28 天約 8,352（5 分鐘），90 天約 8,736（15 分鐘）——所以 28 天、90 天都是每人每條線約 33–35 KB。舉例
（人數與線數是假設）：1,000 人、每人平均出現在 8 條線，約 270 MB，加上讀到的列（`max_rows` 的 500 萬列約
300 MB）。`max_rows` 管的是後一項，不是前一項；三個期間最多同時各留 10 分鐘。算的時候，分析自己的暫存再多約一到
兩成；來源若把整個查詢結果做成一個 list 回傳，峰值還要加上那一整份——請逐頁 yield。

## 7. 設定

`capacity:` 的每個 key 在 [configuration.md](configuration.md#15-capacity容量規劃)；sandbox-host 的兩個環境變數也在那裡。
