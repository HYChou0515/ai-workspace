# Plan — capacity: who uses how much of what, where, and what more people would need

**Status:** grilled 2026-10-15 on master `15064eba`; P1–P15 built in #962 (one PR, D14).
Where the build differs from this plan, §19 says so and why — §19 wins over the sections above.

**Markers:** **[user]** = the user's words or explicit choice, quoted · **[verified]** =
read from the code at `15064eba` · **[proposal]** = mine, overridable — say so and it
changes · **[source]** = a cited publication or vendor document (§18).

## 1. The question

The user wants to measure first and plan purchases from the measurement **[user]**:

> 我們的目的是要統計 每個人的使用量 以此來估計我們的系統能夠承受多少人使用 並且如果未來要買新機器
> 需要多少 再來 我們未來如果要買多少gpu 要買多少 … 我們需要先量測 才能量化這些

> 我們的介面上要能看了就能回答 我們吃掉多少資源 如果有更多人 我們需要多少資源

So the page answers two questions without anyone doing arithmetic:

1. **What do we consume now?** For each resource (CPU, memory, disk, GPU), how much each
   person used, in each place (API, worker, sandbox, Postgres, GPU).
2. **What would more people need?** How many machines and GPUs for N people, which
   resource runs out first, and by when we must order.

What exists today **[verified]**:

- `/my-resources` and `_SandboxActivity` show each sandbox's *allowance*
  (`cpu_milli`/`memory_bytes`, `api/sandbox_activity.py:115-136`), not what it used.
  Nothing reads a cgroup's `cpu.stat` or `memory.current`.
- No history: disk (`quota/disk_ledger.py:71-79`) and sandboxes are current values only.
- Tokens are stored per message (`MessageMetrics`, `resources/conversation.py:8`) and
  never aggregated. There is no Prometheus, `/metrics` or OpenTelemetry endpoint.
- The platform configures no logging format (no `basicConfig`/`dictConfig`, no JSON
  formatter; `uvicorn.Config` has no `log_config`, `__main__.py:522`).

## 2. Decisions the user made

Only what the user said or chose. Everything I filled in is in §3.

| # | Decision | Source |
|---|---|---|
| D1 | Both the platform's resources and the LLM/GPU side are in scope. The purpose is capacity planning: how many people the system carries, how many machines and GPUs to buy. | **[user]** 「C 但是你要設計interface我們補實作」; 「我們的目的是要統計 每個人的使用量 …」 |
| D2 | The platform defines interfaces; the operator implements all four data sources — ① the platform's usage records in ES, ② pod/machine CPU and memory plus machine capacity, ③ GPU utilisation and memory per card, mapped to the model it serves, ④ LiteLLM's request log. All four at ≥ 5-minute resolution (1 minute is better). | **[user]** 「全部都讓我們自己實作 你只要定義好interface」; ②③④ 「都可以」 |
| D3 | The platform adds no storage. Usage records are log lines; the operator's collector ships them to ES. | **[user]** 「我不想存耶」; 「用量紀錄要怎麼送進你們的 ES？」→「A」 |
| D4 | The dashboard is in the platform, for superusers only. | **[user]** 「我希望c, 因為我們無法在grafana新增圖表」; 「dashboard 給誰看？」→「A」 |
| D5 | Attribution (person, App, item, model) is the platform's job: the operator's systems cannot tell items apart. | **[user]** 「我們又無法區分item.. 你可以動腦袋嗎」 |
| D6 | No money. | **[user]** 「Litellm已經有記錄了 需要的話我們只需要取」 |
| D7 | LiteLLM is a standalone proxy, its log is in its own database, and every system that shares the GPUs goes through it. | **[user]** proxy →「A」; 存在哪裡 →「B 而且已經有系統在統計每個人的用量」; 共用 GPU 的系統 →「A」 |
| D8 | Components: API pod, sandbox-host, worker pod, Postgres, and the GPUs. Resources: CPU, memory, disk, GPU. The page shows, for every resource, how much each person used in each place. | **[user]** 「我覺得整個api server和沙盒等是需要考量的對象」; 「好 我們會想知道cpu/mem用量 Disk usage用量 我希望最後至少要能看出來每種resource 每個人在什麼地方(api sandbox, …)用了多少」; 「Gpu也是」 |
| D9 | No load test: everyday traffic is the measurement. The GPUs are shared with other systems. | **[user]** 「為什麼不能用平常流量來作為測量基準即可？我們平常就會感受到人多會慢了」; GPU 共用 →「B」 |
| D10 | Purchases are sized from the whole distribution, not an average. API and worker usage is split per person by counting requests in a middleware and solving for each kind's cost by regression (the utilization law) — replacing the sampling profiler agreed earlier. | **[user]** 「但買多少機器不是用平均值？而是要看整個分布外推才對吧」; 改用 middleware 數次數＋迴歸 →「可以」 |
| D11 | Nine views (§10), in five tabs: 總覽｜分攤｜趨勢｜試算與時程｜門檻與可信度. Fixed overhead is a toggle on the page — separate / proportional / even — as Kubecost and OpenCost do it. | **[user]** 「我們會需要多重視圖才對 你應該要幫我們想好…」→ the list →「看起來ok」 |
| D12 | "N people" = people who used the platform at least once in the last 30 days. | **[user]** 「A」 |
| D13 | All five kinds of slowness are measured. (a) general operations, (b) send → first output, (d) sandbox runs slower, (e) background jobs wait — are felt most and shown first; (c) tokens per second is secondary. | **[user]** 「Abde特別有感」 |
| D14 | One delivery. Views that need history show how much has accumulated until there is enough. | **[user]** 「都一起做掉 我希望能直接使用」 |
| D15 | Charts use ECharts, loaded lazily by this dashboard only, `^5.6` like the chart plugin. The guard that keeps ECharts out of the SPA is adjusted, not deleted. The tool-usage panel (#932) stays as it is. De-duplicating the chart plugin's own copy is later. | **[user]** 「用echart吧 之前說不要是因為只有少數需要 現在越來越多」; the loading approach →「Ok」 |

## 3. My proposals (overridable)

| # | Proposal | Why |
|---|---|---|
| E1 | Each record is one JSON object on one stdout line (§5), and every number in a windowed record is **additive**. | The platform sets no log format **[verified]**, so a complete JSON line is the one shape that survives whatever logging a deploy adds. Additive numbers make *sum* the only aggregation ES has to do. |
| E2 | Interface shapes (§8): ① has two methods (grouped sums; raw rows of the two low-volume kinds); ②③④ return fixed-shape rows. All are synchronous and are called off the event loop. | One ES composite aggregation and one paged search implement ①; plain SQL implements ④. |
| E3 | The middleware records the **route template**; grouping into kinds happens at analysis time. | No class table to go stale when a route is added. |
| E4 | The person is whoever `get_user_id` resolves for the request. When it cannot be resolved the record says `unknown`; work no person asked for says `system`. Both are rows of their own on the page and their share is in view 9. | A gap must be visible, not spread silently over everyone. |
| E5 | **GPU (this changes what I proposed during the grill):** a person's GPU use = the seconds their requests were in flight on that model (④, by tag); the capacity of a model = its in-flight level at the latency knee, per card (view 8). It is **not** a regression of GPU utilisation on tokens. | nvidia-smi's utilisation is "percent of time over the past sample period during which one or more kernels was executing" **[source]**, so a single decoding request keeps it near 100%; it does not rise with load the way CPU does. ③ still gives the card count per model, the memory, and a cross-check. |
| E6 | The analysis runs on demand inside the API process, in a thread (`asyncio.to_thread`), one at a time per period, cached in memory for 10 minutes. Nothing is written. | D3. A worker job would need a result store and a new JobType and deployment; superusers open this rarely; a row cap bounds memory (§11). |
| E7 | Default period 28 days; 7 and 90 selectable. Resolution 5 minutes; records are written per 5-minute window (`capacity.window_s`; lower it to 60 when ② is 1-minute and ES has room). | D2's ≥ 5-minute floor; ES volume (§5.3). |
| E8 | "Peak" for a person = their mean demand over the component's busiest 5% of slots; "average" = over all slots. | Answers "who drives the peak", which is what capacity is bought for. |
| E9 | Safe threshold = the measured knee (view 8). Until a knee is seen: CPU 70%, memory 80%, disk 80% — my numbers, labelled as defaults on the page. GPU has no default: its capacity is unknown until a knee is seen. | |
| E10 | "N people" what-if = a two-stage bootstrap (§9.6): draw a day, then draw N people's whole days from that day's 30-day-active population, zero days included; 400 draws; p50 and p95 of the day's peak. When a work calendar is set, only workdays are drawn. | Keeps time-of-day alignment, busy days, heavy users and absent users, as the ICU-capacity bootstrap does **[source]**. |
| E11 | Backtest: rolling one-day-ahead over the last 14 days. A mean absolute percentage error above 20% marks views 6 and 7 「僅供參考」. | 20% is the edge of the "good" band in Lewis's forecast-accuracy scale **[source]**. |
| E12 | Purchase lead time is an input on the page (default 90 days), not config. | It changes per purchase. |
| E13 | "One more machine" = the unit ② reports in `capacity()` (`machine_label`). | The operator knows what they buy. |
| E14 | LiteLLM tags go in the `x-litellm-tags` header, which lands in `request_tags` **[source]**. No item tag. A knob turns tagging off. | Thousands of item ids would become thousands of tags in LiteLLM's per-tag tables. The knob covers tag routing (§7). |
| E15 | Sandbox contention is measured as CPU **pressure** (PSI), not throttling. Both are recorded. | Throttling means a sandbox hit its own `cpu.max` — its limit is low. Pressure means it waited for a busy machine — buy machines. View 8's (d) uses pressure. |
| E16 | Postgres memory and disk, and GPU memory, are not split per person. They are shown as fixed, with a growth trend. | Splitting Postgres disk would need a scan of every table; model weights and the preallocated KV cache belong to nobody. |
| E17 | The middleware's cost is measured by a benchmark script and reported in the PR. Recording is on by default; `capacity.record: false` turns it off. | D14 「直接使用」. |
| E18 | Days are counted in `capacity.timezone` (IANA name; empty = UTC). | A person's day must not be cut at UTC midnight in another zone. |

## 4. How it works

```
platform processes                 operator's systems                     platform, on demand
──────────────────                 ──────────────────                     ───────────────────
API pod   ─ work · turn · job ─┐
worker    ─ work · job ────────┤
sandbox-host ─ sandbox ────────┼─ stdout JSON ─► collector ─► ES ──① UsageRecordSource ─┐
disk-ledger pass ─ disk ───────┘                                                          │
                                   pod / node metrics ──────────────② ResourceMetricsSource┤
LLM calls ─ x-litellm-tags ─► LiteLLM proxy ─► LiteLLM DB ─────────④ LlmRequestSource ───┼─► analysis ─► /api/admin/capacity ─► /capacity
                                   GPU exporter ────────────────────③ GpuMetricsSource ────┘          (superusers)
```

Three kinds of resource, three ways of putting each unit on a person:

1. **Already separate per person — measured directly.** Each sandbox has its own cgroup;
   each workspace and KB collection has its own bytes; each LLM request carries its
   person's tags.
2. **Shared by everyone — counted, then solved.** The API, the workers and Postgres serve
   everyone from one process. The middleware counts each person's requests per kind per
   window; ② gives each pod's CPU per window; a regression over thousands of windows
   gives each kind's cost per request (the utilization law: busy time = Σ cost × count
   **[source]**). A person's share = Σ cost × their count.
3. **Belongs to nobody — fixed.** Idle processes, Postgres's cache, model weights. Shown
   as its own row, or spread by the page's toggle (D11).

## 5. What the platform records (schema v1)

### 5.1 Envelope

```json
{"@timestamp":"2026-10-15T04:05:00.000Z","event":{"dataset":"workspace.usage"},"usage":{"v":1,"kind":"work","pod":"api-7f9c","component":"api", ...}}
```

- `@timestamp` is the **start** of the interval the numbers describe (windowed kinds), or
  the moment of the event. Windows are aligned to multiples of `window_s` since the epoch,
  so a date histogram at 5 minutes puts every window in exactly one bucket.
- `event.dataset` is the constant the collector routes on (D3 「A」).
- Every record has `usage.v`, `usage.kind`, `usage.pod`, `usage.component`
  (`api` | `worker` | `sandbox-host`).

### 5.2 Kinds

**`work`** — per window, per (person, App, work), from every API and worker process.

| Field | Meaning |
|---|---|
| `user`, `app` | person (E4); App slug or `""` |
| `work` | what was done, below |
| `count`, `seconds` | additive; meaning per `work` below |
| `h0` … `h9` | requests only: latency histogram, omitted when 0. Upper bounds in ms: 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, ∞ |
| `window_s` | window length |

| `work` | `count` | `seconds` |
|---|---|---|
| `req:<METHOD> <route template>` | requests finished | their wall time |
| `stream:<route template>` | connections opened | connection time inside the window (an open connection is charged at every flush) |
| `turn:<surface>` | turns started (`chat`, `kb`, `workflow`) | running time inside the window |
| `turn-events:<surface>` | events published | — |
| `job:<jobtype>` | jobs started | running time inside the window |
| `req:GET (static)` / `req:<METHOD> (unmatched)` | the SPA's files; requests no route matched | wall time |

**`turn`** — one per turn, at its end: `user`, `app`, `item`, `surface`, `outcome`
(`done` | `error` | `cancelled`), `accepted_ms`, `started_ms`, `first_event_ms`,
`first_output_ms` (the first `message_delta` or `tool_start`; null if none), `ended_ms`,
`model`, `prompt_tokens`, `completion_tokens`.

**`job`** — one per attempt, at its end: `user`, `jobtype`, `enqueued_ms`, `started_ms`,
`ended_ms`, `outcome` (`done` | `failed`), `attempt`.

**`sandbox`** — per window, per live sandbox: `item`, `user`, `app`, `sandbox`, `cpu_s`,
`mem_bs` (memory × seconds), `mem_max` (the one non-additive field: aggregated with max),
`thr_s`, `thr_n`, `periods`, `cpu_psi_s`, `mem_psi_s`, `oom`, `cpu_limit` (cores),
`mem_limit` (bytes), `window_s`.

**`sandbox_host`** — per window, per sandbox-host pod: `live_s` (sandbox-seconds, so
`live_s / window_s` = sandboxes alive on average), `cpu_psi_s`, `mem_psi_s` (the host's
`/proc/pressure`), `window_s`.

**`disk`** — a snapshot, from each disk-ledger pass: `scope` (`workspace` | `kb`), `item`
or `collection`, `user`, `app`, `bytes`. Not additive over time: the analysis keeps each
id's latest value per day.

**`meta`** — when something was lost in a window: `dropped` (rows dropped because the
write queue was full), `window_s`.

### 5.3 Volume

Rows per window ≈ Σ over processes of the distinct (person, work) pairs active in it, plus
one per live sandbox. Worked example, every input an assumption: 40 people active at once
for 10 hours a day, each touching 8 route templates per 5 minutes, spread over 3 API pods
→ 40 × 8 = 320 rows per window, 120 windows → 38,400 `work` rows a day; 100 sandboxes for
10 hours → 12,000 `sandbox` rows. The real figure comes from the first day of records;
view 9 shows it.

## 6. Where each record comes from

### 6.1 The middleware (`work` for requests and streams)

A pure ASGI middleware (`capacity/middleware.py`), added after `PerfTraceMiddleware` so it
is outermost (`api/app.py:1500-1529` **[verified]**).

- **Route.** FastAPI sets `scope["route"]` on the same scope dict the middleware holds,
  during routing (`fastapi/routing.py:807`, `starlette/routing.py:679` **[verified]**), so
  the template is read in the `send` wrapper, never on entry. No route and a path outside
  `/api` = `(static)`; no route under `/api` = `(unmatched)`.
- **Stream or request.** A response whose `content-type` is `text/event-stream` is a
  stream: it is tracked as in flight from `http.response.start` until it ends. This covers
  all nine long-lived routes **[verified]** without a list to maintain. Everything else is
  a request, measured from entry to the last body chunk.
- **Cost.** One dictionary update per request, plus the in-flight bookkeeping per stream.
  Measured by `scripts/bench_usage_middleware.py` (E17).

### 6.2 Who made the request

`get_user_id` is a zero-argument callable; a deploy passes its own cookie reader
(`__main__.py:125`, `create_app(get_user_id=…)` **[verified]**). The middleware never
guesses:

1. On entry it puts a fresh *holder* in a ContextVar.
2. `create_app` wraps the `get_user_id` it was given: the first value it returns during a
   request is written into that request's holder. Routes already call it, so this costs
   nothing extra.
3. After the response, a holder that is still empty gets one call of `get_user_id()`;
   an exception means `unknown`.
4. The holder is **closed** when the request ends. Tasks the request started copy the
   context — including the holder — so a closed holder ignores later writes and reads as
   "no person". Otherwise a long-lived task started by one person's request (the
   per-conversation turn worker) would attribute everyone after them to that person.

### 6.3 Turns

All three surfaces enqueue on one engine **[verified]**: app chat (`chat_send.py:1558`), KB
chat (`kb_chat_routes.py:1099`), workflow steps (`workflow_exec.py:345`).

- `ChatTurnEngine.enqueue` gains `surface` and `accepted_ms`. `accepted_ms` is when the
  user's message was persisted — the acceptance (`chat_send.py:906` **[verified]**).
- `started_ms`: when `_worker` creates the turn's task (`turns.py:989`).
- `first_event_ms` / `first_output_ms`: the first `publish` in `_run_turn`
  (`turns.py:1125-1127`), and the first `message_delta` or `tool_start`. The gap between
  `accepted` and `first_event` is the platform's part of (b); between `first_event` and
  `first_output`, the model's.
- The turn's task sets the usage scope (person = `ctx.speaker` or `ctx.acting_user`, App =
  `ctx.app_slug`, item = `ctx.investigation_id`, purpose = surface) for its LLM calls.
- `turn` and `turn-events` work are charged to the running pod while the turn runs; the
  `turn` record is written at the end, cancelled and failed turns included.

### 6.4 Jobs

Nine job types register `job_handler=self._handle` **[verified]**: `kb/index_coordinator.py:160`,
`kb/wiki/coordinator.py:232`, `kb/card_gen_coordinator.py:158`, `kb/import_jobs.py:231`,
`kb/eval/coordinator.py:80`, `kb/graph/coordinator.py:69`, `health/sanity/coordinator.py:93`,
`filestore/blob_gc.py:132`, `chat_video/jobs.py:143`.

- Each becomes `job_handler=recorded_job("<jobtype>", self._handle)`. The wrapper sets the
  usage scope (person = `job.info.created_by`, purpose = jobtype), tracks `job:<jobtype>`
  while running, and writes the `job` record. Queue wait = `started_ms` −
  `job.info.created_time` (specstar records no start time **[verified]**).
- Eval, graph, sanity and blob-gc create their jobs without `using(...)` **[verified]**, so
  their `created_by` is whatever the spec's default user resolves to where they were
  enqueued — the requester inside a request, the fallback user in a sweeper. Recorded as
  is; not fixed here.
- A test lists every `job_handler=` in `src/` and fails if one is not wrapped.

### 6.5 Sandboxes

sandbox-host already gives each sandbox its own cgroup, `<cgroup_root>/<remote_id>`, and
knows each sandbox's item (`sandbox-host/src/sandbox_host/isolated_process.py:95-151`,
`app.py:421-431` **[verified]**). Nothing reads its usage today.

- **One reader, two copies.** `sandbox/cgroup_usage.py` reads a cgroup (`cpu.stat`:
  `usage_usec`, `nr_periods`, `nr_throttled`, `throttled_usec`; `memory.current`;
  `memory.events`: `oom_kill`; `cpu.pressure` and `memory.pressure`: `some total=`;
  `cpu.max`; `memory.max`) and integrates successive readings into a window's sums. A
  missing file (a kernel without PSI) leaves that field out. A counter that went backwards
  is a new cgroup and counts from zero. sandbox-host carries a byte-identical copy
  (`sandbox_host/cgroup_usage.py`); a test compares the bytes.
- **sandbox-host samples itself** every `SANDBOX_HOST_USAGE_SAMPLE_S` (default 60; 0 =
  off) and writes one `sandbox` row per sandbox per `SANDBOX_HOST_USAGE_WINDOW_S` (default
  300), plus one `sandbox_host` row.
- **Who owns it.** `SandboxSpec` (`sandbox/protocol.py:106`) gains `labels` (person, App),
  default empty, so no `Sandbox` method changes signature. The registry fills them from
  `ItemLocator.owner_of` — the same rule the quota charges by (`api/locator.py:190`) — and
  the App slug. `HttpSandbox.create` sends them; sandbox-host keeps them beside `_item_of`.
- **`kind: local` with isolation** (`sandbox/isolated_process.py`, cgroup `<root>/<item_id>`)
  samples with the same module from a pod-local loop. `kind: local` without isolation,
  `docker` and `mock` have no per-sandbox cgroup: no `sandbox` rows, and the page says so.

### 6.6 Disk

The disk-ledger pass already visits every item, on the blob-gc worker every 6 hours
(`quota/disk_reconcile.py:128`, `resources.disk_reconcile_interval_sec`, **[verified]**).
After it reconciles, it writes one `disk` row per item and one per KB collection — the
collection's bytes are one grouped sum of `content_size` in the database
(`kb_routes.py:1078-1080` **[verified]**). No new loop, and the work stays on a worker
(the #804 convention).

### 6.7 Flushing

Each process keeps its counters in memory and writes a window's rows when the window
closes, and again on SIGTERM. Rows go through a bounded queue to a writer thread, so a slow
stdout never blocks a request or the event loop; a full queue drops rows and counts them
(`meta`). The loops are pod-local: the API's lifespan, the worker runner, sandbox-host's
lifespan.

## 7. LiteLLM tags

Every LLM call through the platform's four adapters carries

```
x-litellm-tags: ws,ws.user:<id>,ws.app:<slug>,ws.purpose:<chat|kb|workflow|request|<jobtype>>,ws.call:<agent|llm|vlm|embed>,ws.component:<api|worker>
```

(`,` and `%` inside a value are percent-encoded), read from the usage scope at the moment
of the call. The four adapters **[verified]**: the agent path (`_build_model`,
`api/litellm_runner.py:746`, wrapped per call the way `HeaderModel` already adds headers,
`agent/header_model.py:27`), `LitellmLlm.stream` (`kb/llm.py:116`), `LitellmVlm.stream`
(`kb/vlm/litellm.py:43`) and `LitellmEmbedder._embed_at` (`kb/embedder.py:212`).

- **Context across threads.** `asyncio.to_thread` copies the context; a bare
  `run_in_executor` does not. Each adapter's test calls it the way production does.
- **No scope** (a sweeper, a health check) → `ws.user:system`.
- **Code inside a sandbox** that calls LiteLLM itself is not tagged; it shows under its
  key as untagged traffic.
- **Tag routing.** With LiteLLM's `enable_tag_filtering` on, a request whose tags match no
  deployment goes to the `default` pool, or fails when there is none **[source]**. The
  migration entry says to check this before rollout; `capacity.llm_tags: false` turns
  tagging off.

## 8. The four interfaces the operator implements

`capacity/sources.py`. Each is configured by dotted path and loaded by
`factories._construct_dotted` (zero-argument constructor, subclass check;
`factories.py:1431-1458` **[verified]**), like `server.notification_channel`. All methods
are synchronous and run in a worker thread; times are epoch milliseconds; bucket starts
are aligned to multiples of `step_s` since the epoch.

```python
class UsageRecordSource(ABC):  # ① the platform's own records, read back from ES
    @abstractmethod
    def aggregate(self, q: AggregateQuery) -> Iterable[AggregateRow]:
        """Sum (and max) `q.sums`/`q.maxes` over records of `q.kind` in
        [start_ms, end_ms), grouped by `q.group_by` and by `step_s` buckets."""

    @abstractmethod
    def records(self, kind: str, start_ms: int, end_ms: int) -> Iterable[Mapping[str, Any]]:
        """Every `usage` object of `kind` ("turn" or "job") in the range."""

@dataclass(frozen=True)
class AggregateQuery:
    kind: str                       # "work" | "sandbox" | "sandbox_host" | "disk"
    start_ms: int
    end_ms: int
    step_s: int | None              # None = one bucket for the whole range
    group_by: tuple[str, ...]       # e.g. ("user", "work"), ("pod", "work")
    sums: tuple[str, ...]
    maxes: tuple[str, ...] = ()
    where: Mapping[str, str] = {}   # exact match, e.g. {"user": "alice"}

@dataclass(frozen=True)
class AggregateRow:
    t_ms: int | None
    keys: Mapping[str, str]
    values: Mapping[str, float]


class ResourceMetricsSource(ABC):  # ② pods and machines
    @abstractmethod
    def usage(self, start_ms: int, end_ms: int, step_s: int) -> Iterable[ResourceSample]: ...
    @abstractmethod
    def capacity(self) -> Iterable[CapacityEntry]: ...

@dataclass(frozen=True)
class ResourceSample:
    t_ms: int
    component: str          # "api" | "worker" | "sandbox-host" | "postgres" | "filestore" | "scratch"
    pod: str                # pod or volume name
    cpu_cores: float | None     # mean over the bucket
    memory_bytes: float | None  # mean over the bucket
    disk_bytes: float | None    # used, at the bucket (volumes)

@dataclass(frozen=True)
class CapacityEntry:
    component: str
    cpu_cores: float | None
    memory_bytes: float | None
    disk_bytes: float | None
    machines: int           # how many units make that total
    machine_label: str      # what one more unit is, e.g. "node 32 cores / 128 GiB"


class GpuMetricsSource(ABC):  # ③ cards
    @abstractmethod
    def usage(self, start_ms: int, end_ms: int, step_s: int) -> Iterable[GpuSample]: ...
    @abstractmethod
    def cards(self) -> Iterable[GpuCard]: ...

@dataclass(frozen=True)
class GpuSample:
    t_ms: int
    card: str
    model: str              # the model name as LiteLLM reports it
    util: float             # 0..1, mean over the bucket
    memory_bytes: float

@dataclass(frozen=True)
class GpuCard:
    card: str
    node: str
    model: str
    memory_total_bytes: float


class LlmRequestSource(ABC):  # ④ LiteLLM's request log
    @abstractmethod
    def usage(self, start_ms: int, end_ms: int, step_s: int) -> Iterable[LlmUsageRow]: ...
    @abstractmethod
    def latency(self, start_ms: int, end_ms: int, step_s: int) -> Iterable[LlmLatencyRow]: ...

@dataclass(frozen=True)
class LlmUsageRow:          # grouped by bucket × model × the request's whole tag list
    t_ms: int
    model: str
    tags: tuple[str, ...]   # () for untagged traffic
    requests: int
    prompt_tokens: int
    completion_tokens: int
    busy_seconds: float     # Σ (end − start) of the requests that started in the bucket

@dataclass(frozen=True)
class LlmLatencyRow:        # grouped by bucket × model
    t_ms: int
    model: str
    requests: int
    ttft_p50_s: float | None
    ttft_p95_s: float | None
    tokens_per_s_p50: float | None
```

**Checking an implementation.** `uv run python -m workspace_app.capacity check [--hours 24]`
builds the configured sources and calls each method over the last N hours. It reports
misaligned buckets, unknown components, negative or impossible values (a pod using more
CPU than its machine has), `ws.*` tags that do not parse, gaps, and how many rows each
call returned. Exit 1 on an error. `docs/capacity.md` gives the record mapping and an
example for each source (an ES composite aggregation, PromQL, DCGM exporter fields, SQL
over `LiteLLM_SpendLogs`) as starting points, not shipped code.

## 9. The analysis

### 9.1 Each component × resource, and how a person's share is found

| Component | Resource | Total from | A person's share |
|---|---|---|---|
| API | CPU | ② per pod | regression on `work` counts (§9.3) |
| API | memory | ② per pod | regression on concurrency: streams, turns and in-process jobs in flight (seconds ÷ step) |
| worker | CPU, memory | ② per pod | per job type, regression on `job:<type>` seconds; a pod's job type is read off its `work` rows |
| Postgres | CPU | ② | regression on every API and worker count, pooled |
| Postgres | memory, disk | ② | fixed; disk with its growth per active person per month (E16) |
| sandbox-host | CPU, memory | ① `sandbox` rows; ② for the pod | direct. Pod − Σ sandboxes = the host's own overhead, fixed |
| filestore / scratch | disk | ① `disk` rows; ② for the volume | direct (workspace bytes + KB bytes). Volume − Σ = unattributed, fixed |
| GPU | compute | ④ `busy_seconds` per model | direct, by tag: a person's in-flight seconds; untagged and other systems' traffic is its own row (E5) |
| GPU | memory | ③ | fixed (E16) |

Units on the page: CPU in cores (mean over a slot), memory and disk in bytes, GPU in
requests in flight (mean over a slot) and, through the knee, in cards.

### 9.2 Slots

The period `[end − days, end)` is cut into slots of `capacity.step_s` (default 300 s,
a multiple of `window_s`). `end` is now minus `capacity.lag_s` (default 600 s, for ES
ingest lag), floored to a slot. Days are cut in `capacity.timezone` (E18).

### 9.3 Regression (D10)

For each shared component, rows are (pod, slot) and the target is the pod's CPU-seconds in
the slot. Columns are the work kinds: a route template becomes its own column when it has
at least 200 requests in the period and appears in at least 5% of slots; the rest merge
into one column per method. Streams use connection-seconds, turns use events, in-process
jobs use running seconds. Each pod has its own intercept (its idle cost). Coefficients
must be non-negative: non-negative least squares (Lawson–Hanson **[source]**), written on
numpy. numpy is already in the image by way of llama-index-core and gets declared; scipy is
only in the `process-sandbox` extra, which the API image does not install
(`docker/Dockerfile:81` **[verified]**).

A person's CPU in a slot = Σ over kinds of (cost × their count in that slot). What the
regression does not explain (actual − fitted) is a row of its own, so every table adds up
to the measured total. Accuracy is limited by how much the mix of requests varies between
slots, and regression-based estimates are known to be less accurate than direct
measurement **[source: Spinner et al.]**; view 9 shows the fit and its backtest.

### 9.4 Allocation (views 2, 3)

- **Average** = mean over all slots; **peak** = mean over the component's busiest 5% of
  slots (E8).
- **Fixed overhead** (D11): *separate* (default) — its own row; *proportional* — split by
  each person's variable use; *even* — split equally over the people active in the
  last 30 days. `unknown` and `system` take part in *proportional* only.
- Grouping: person, component, App, purpose. Purpose = the work kind (requests, streams,
  each turn surface, each job type, sandbox, disk, each model's GPU).

### 9.5 Active people (D12)

A person is active on day *d* when any record carries their id in the 30 days before *d*
(`unknown` and `system` excluded). N(*d*) is the count; it is both the x-axis of the trend
and the "N" of the what-if.

### 9.6 What-if (view 6) and headroom (view 1)

For each person and day there is a *profile*: their demand in every slot of the day, for
every variable line (API CPU, API memory, each worker type, Postgres CPU, sandbox CPU,
sandbox memory, each model's GPU). A person active in the last 30 days who did nothing that
day has a zero profile.

To simulate N people (E10):

1. Draw a day *d* (a workday when a work calendar is set).
2. Keep the day's real population when N ≥ N(*d*) and add N − N(*d*) people drawn with
   replacement from the chosen profile — everyone, the heaviest 10%, or the people of one
   App; for N < N(*d*), draw N from everyone.
3. Add the fixed lines and, for GPUs, the other systems' traffic that day (it does not grow
   with our N).
4. The day's peak for each line = the maximum over its slots.

Repeat 400 times; report p50 and p95 of the peak. Drawing is a matrix product — how many
times each profile was drawn, times the profile matrix — so N's size does not change the
cost. Every N uses the same random draws, so the result never shrinks as N grows.

- **Needed** for a line = its p95 peak ÷ the safe threshold (E9); machines =
  ⌈needed ÷ one machine (E13)⌉; GPU cards = ⌈peak in flight ÷ in-flight per card at the
  knee⌉.
- **Headroom** ("可再撐 X 人") = the largest N whose p95 peak stays under capacity × the safe
  threshold on every line; found by stepping N up from today's and bisecting. The first
  line to fail is the **binding constraint**.

### 9.7 Runway (view 7)

A straight line fitted to N(*d*) over the period, with its 80% prediction band, gives the
date N reaches the headroom; minus the lead time (E12) is the date to order by. Disk gets
its own line per volume, since it grows with time even at constant N.

### 9.8 Thresholds (view 8)

Each pair is (slowness per slot, load per slot):

| | Slowness | Load |
|---|---|---|
| a | p95 of general requests (no streams), from `h0…h9` | API CPU utilisation (② ÷ capacity) |
| b | p95 of `first_output_ms − accepted_ms` | API CPU utilisation; and each model's requests in flight |
| c | median tokens per second (④) | that model's requests in flight |
| d | sandboxes' CPU pressure share (p95 across sandboxes) | the sandbox-host's CPU utilisation |
| e | p95 of job queue wait, per job type | that worker type's CPU utilisation |

A hinge model, y = a + b · max(0, x − k), is fitted with k searched over x's 10th–95th
percentiles (segmented regression **[source: Muggeo]**). A knee counts when it cuts the
squared error of a straight line by at least 20% and has at least 30 slots beyond it;
otherwise the page says 「尚未觀察到變慢（最高 x%）」. (d) also shows throttling — a
sandbox at its own limit — separately (E15).

### 9.9 Confidence (view 9)

- Each regression: how much of the variation it explains, and its error predicting the
  last 7 days from the earlier ones.
- The whole model: for each of the last 14 days, the peak predicted from the days before it
  and that day's N, against the actual peak; the mean absolute percentage error decides
  「僅供參考」 (E11).
- Coverage: the share of slots each source has, and the gaps.
- Attribution gaps: the `unknown` share, untagged LLM traffic, `meta.dropped`.
- The assumptions the numbers rest on, in words.

### 9.10 When a view is ready

| View | Needs | Ready after |
|---|---|---|
| 1 總覽 | ②; headroom also needs view 6 | 1 day; headroom as view 6 |
| 2 分攤, 3 個人明細 | ①; regression lines also ② | direct lines 1 day; regression lines 3 days of overlap |
| 4 分布 | as 2 | 7 days and ≥ 10 active people |
| 5 趨勢 | any one source | 2 days |
| 6 試算 | ① ②; GPU lines also ③ ④ | 14 days, ≥ 10 active people, regressions ready |
| 7 時程 | as 6 | 28 days of N(*d*) |
| 8 門檻 | the pair's two series | 7 days, and the load spans ≥ 20 percentage points |
| 9 可信度 | — | backtest 21 days |

Until then a view shows what it is missing: 「未接資料來源：② …」, or 「已累積 9 天，需要
14 天」. It appears by itself once the data is there (D14).

## 10. The dashboard

`/capacity`, linked for superusers only (`usePlatformDestinations`, beside Work calendar);
anyone else gets the 403 the API returns. Five tabs (D11), the WAI-ARIA tabs pattern.

| # | View | Question | On the page |
|---|---|---|---|
| 1 | 總覽 | How much is used, how far from the limit, which resource runs out first? | A sentence: 「照現在的用法，大約還能撐 X 人（最先不夠：…）」. Component × resource: peak, capacity, safe threshold. Sources connected or not. |
| 2 | 分攤 | Who used how much of each resource, where? | Resource; group by person / component / App / purpose; average or peak; fixed overhead separate / proportional / even. Stacked bars and a table. |
| 3 | 個人明細 | Where does this person's use go; are they heavy? | From a row of view 2: their lines over time per component, their Apps and items, their rank (「前 5%」). |
| 4 | 每人用量分布 | How different are people; how much do the heaviest take? | Peak use per person: histogram, p50 / p90 / p99, 「前 10% 的人用掉幾成」. |
| 5 | 趨勢 | Is use growing, how fast? | Per resource: daily peak and mean against capacity, active people overlaid; disk growth. |
| 6 | 試算 | What would N people need? | N; whose habits (everyone / the heaviest / one App's); p50 or p95. Per component × resource: needed, have, short by, machines or cards, fits or not. |
| 7 | 時程 | When does it run out; can we order in time? | Active people and its trend; the date each limit is reached; lead time; the order-by date. |
| 8 | 變慢的門檻 | At what load does it get slow? | One scatter per pair (a, b, d, e first; c below), the knee marked. |
| 9 | 可信度 | Can these numbers be trusted? | Backtest error, fit, coverage, gaps, attribution gaps, assumptions. |

Tabs: 總覽 (1) ｜ 分攤 (2, 3, 4) ｜ 趨勢 (5) ｜ 試算與時程 (6, 7) ｜ 門檻與可信度 (8, 9).

Copy says what a number means, not how it was computed («這個估計解釋了 87% 的變化», not
"R²").

**ECharts (D15).** `web/src/capacity/echarts.ts` is the only module that imports ECharts
(`echarts/core` and the bar, line and scatter charts, grid, tooltip, legend, mark line,
mark area and data-zoom components, canvas renderer). Everything else reaches it with
`import("./echarts")`, so Vite gives it its own chunk, downloaded when a superuser opens
the page. The guard in `web/src/ext/imports.test.ts` ("the SPA bundle leaves ECharts to the
chart plugin", :292-350 **[verified]**) becomes: ECharts may be imported by that one file
only; every import of that file is dynamic; web's `echarts` range matches the chart
plugin's; no SPA module imports a runtime plugin's files. The chart plugin keeps its own
copy, so a superuser who also opens a chart view downloads ECharts twice (D15, later).

## 11. Server

`api/capacity_routes.py`, all superuser-only with the tool-usage gate's shape (403,
`api/tool_usage_routes.py:143-145` **[verified]**):

- `GET /api/admin/capacity?days=28` — views 1, 2, 4, 5, 8, 9, readiness, and the runway's
  inputs (the page applies the lead time).
- `GET /api/admin/capacity/person?user=<id>&days=28` — view 3.
- `GET /api/admin/capacity/whatif?days=28&n=300&profile=all|heavy|app&app=<slug>` — view 6.
- `POST /api/admin/capacity/refresh?days=28` — drops the cached analysis.

`CapacityService` runs the analysis in a thread, one at a time per period (a second
request waits for the first), and keeps the result — profiles included — for 10 minutes.
`capacity.max_rows` (default 5,000,000) stops an analysis that would load more rows, with a
message that says to shorten the period. A source that raises fails only the views that
need it; the page names the source and the error.

## 12. Config

```yaml
capacity:
  record: true            # write usage records
  sink: ""                # dotted path of a UsageSink; "" = one JSON line per record on stdout
  window_s: 300           # record window; divides 3600
  queue_max: 10000        # rows buffered before dropping
  llm_tags: true          # x-litellm-tags on LLM calls (§7)
  usage_records: ""       # ① dotted path of a UsageRecordSource
  resource_metrics: ""    # ②
  gpu_metrics: ""         # ③
  llm_requests: ""        # ④
  step_s: 300             # analysis slot; a multiple of window_s
  lag_s: 600              # newest data ignored (ingest lag)
  timezone: ""            # IANA; "" = UTC
  max_rows: 5000000
```

sandbox-host: `SANDBOX_HOST_USAGE_SAMPLE_S` (60), `SANDBOX_HOST_USAGE_WINDOW_S` (300).
The implementation PR adds these to `docs/configuration.md` and an entry to
`docs/migrations.md` (new stdout lines, new outbound header, sandbox-host sampling).

## 13. Phases

Flat; one commit or more per phase; one PR (D14).

- **P1 — Records and the writer.** `capacity/records.py` (the kinds of §5, the envelope),
  `capacity/sink.py` (`UsageSink`, the stdout sink, the bounded queue and writer thread,
  drop counting), `capacity/scope.py` (the usage scope ContextVar), the `capacity`
  settings with validation (`window_s` divides 3600, `step_s` a multiple of it).
- **P2 — Counters and the middleware.** The per-process window accumulator with in-flight
  charging; the middleware (§6.1); the identity holder and the wrapped `get_user_id`
  (§6.2); flush loops in the API lifespan and the worker runner, and on SIGTERM; the
  benchmark script.
- **P3 — Turns.** `enqueue(surface=, accepted_ms=)` at the three call sites; the stamps;
  `turn` / `turn-events` work; the `turn` record; the turn's usage scope.
- **P4 — Jobs.** `recorded_job` on the nine handlers; the `job` record and `job:` work;
  the guard over every `job_handler=`.
- **P5 — Sandboxes.** `sandbox/cgroup_usage.py` and its byte-identical sandbox-host copy;
  sandbox-host's sampler and its environment knobs; `SandboxSpec.labels` filled by the
  registry and sent by `HttpSandbox`; the pod-local sampler for `kind: local` with
  isolation.
- **P6 — Disk.** `disk` rows from the disk-ledger pass.
- **P7 — LLM tags.** The header at the four adapters; `capacity.llm_tags`.
- **P8 — Sources.** The four ABCs, their loading from config, the `check` command, the test
  fakes.
- **P9 — Analysis core.** Loading into slot arrays, the regressions, the direct lines,
  allocation with its modes, active people, readiness.
- **P10 — What-if, headroom, runway.**
- **P11 — Thresholds and confidence.**
- **P12 — Routes and the service.** Cache, one-at-a-time, row cap, response models.
- **P13 — Page shell.** Route, nav entry, tabs, the readiness states, the ECharts module
  and the guard's new rule, the API client and query keys.
- **P14 — The nine views.**
- **P15 — Docs.** `docs/capacity.md` (what the page answers; the record schema and its ES
  mapping; an example per source; the check command; the tag-routing caveat),
  `configuration.md`, `migrations.md`, the `mkdocs.yml` nav, this plan's status.

## 14. Tests

- **Records.** Golden lines per kind. Splitting a window in two and adding the halves gives
  the whole, for every additive field.
- **Writer.** A full queue drops and counts, and `write` never waits on a blocked stdout
  (bounded by a deadline, not a speed claim).
- **Middleware.** The template is read after routing; an event stream is a stream; a route
  that calls `get_user_id` attributes with no extra call; one that doesn't gets one call; a
  reader that raises gives `unknown`; a task started by a request cannot attribute after
  the request ends. Each through the real app with `TestClient`, asserted on emitted rows.
- **Turns.** Through `enqueue` with `ScriptedAgentRunner`, for each surface: stamps in
  order, `first_output` only from `message_delta`/`tool_start`, cancelled and failed turns
  recorded.
- **Jobs.** Every `job_handler=` wrapped — proved by deleting the wrapper on one line and
  watching exactly that test redden. Scope set inside the handler.
- **Sandboxes.** A fake cgroup tree on disk: integration, counter reset, no PSI; the copy's
  bytes; labels from the registry to the host to the row.
- **Tags.** Each adapter called the way production calls it, across its thread hop; the
  knob; percent-encoding.
- **Analysis.** A synthetic world with known per-kind costs, idle costs and people with a
  daily rhythm: the regression recovers the costs within tolerance; every allocation adds
  up to the total; the bootstrap's p50 for N identical people is N × one person's peak;
  results never shrink as N grows; the heaviest-10% profile needs at least as much as
  everyone's; a hinge is found where one was put and none where none was.
- **Routes.** 403 for a non-superuser, proved by mutating the gate line.
- **Page.** Each view from a fixture model, each readiness state, ECharts mocked at the
  lazy module; the guard's new rule tested both ways.

## 15. Not doing

- Storing anything, a platform-side ES client, money, load tests, Grafana panels (D3, D6,
  D9, D4).
- A per-person view of one's own use in `/my-resources` (D4: superusers only).
- An AI side — a tool the in-app AI could call to summon this page. Its data is
  superuser-only and lives in no workspace, so `show_file` does not reach it; a later plan
  can add a superuser tool.
- Per-item LLM tags (E14); I/O load on NFS; the `docker`, `mock` and non-isolated `local`
  sandboxes (no per-sandbox cgroup).
- De-duplicating the chart plugin's ECharts (D15).

## 16. What this cannot see

- A regression is only as good as the variety of the traffic it learns from; a new App with
  new habits is outside it until its days are in the period. View 9 shows the error, and
  view 6's "one App's people" profile is how to ask about it.
- The `unknown` share depends on the deploy's `get_user_id` reader. Its first day in
  production says how large it is.
- LiteLLM's tag columns depend on its version; the `check` command shows whether `ws.*`
  tags arrive.
- A kernel without PSI leaves (d) with throttling only, and the page says so.
- Clocks: sources are joined by 5-minute slots, which tolerates small skew, not minutes.

## 17. Ground truth (`15064eba`)

| Fact | Where |
|---|---|
| Middlewares added; the last added is outermost | `api/app.py:1500-1529` |
| `get_user_id` is a zero-argument callable; a deploy overrides it | `__main__.py:125`, `:242` |
| The route is on the scope after routing | `fastapi/routing.py:807`; `starlette/routing.py:679` |
| Nine long-lived routes stream SSE | `chat_routes.py:150, :344`; `kb_chat_routes.py:1108`; `workflow_routes.py:653`; `meta_routes.py:213`; `wui_routes.py:321, :465`; `file_routes.py:867`; `kb_routes.py:1449` |
| A message is accepted when persisted | `chat_send.py:906`, `:1595` |
| Three surfaces enqueue on one engine | `chat_send.py:1558`; `kb_chat_routes.py:1099`; `workflow_exec.py:345`; `turns.py:885` |
| A turn starts; its first event is published | `turns.py:989`; `turns.py:1125-1127` |
| First output events | `api/events.py:24` (`MessageDelta`), `:33` (`ToolStart`) |
| Nine job handlers | §6.4's list |
| Job enqueue time and requester | `job.info.created_time`, `created_by`; `index_coordinator.py:207-209`, `:424` |
| sandbox-host cgroups and item map | `sandbox-host/src/sandbox_host/isolated_process.py:95-151`; `app.py:100`, `:421-431`, `:459` |
| A sandbox's spec | `sandbox/protocol.py:106` |
| Who an item's resources are charged to | `api/locator.py:190` |
| Disk ledger and its pass | `quota/disk_ledger.py:71-79`; `quota/disk_reconcile.py:128` |
| KB bytes per collection | `kb_routes.py:1078-1080` |
| LLM adapters | `api/litellm_runner.py:746`, `:756`, `:761-762`; `agent/header_model.py:27`; `kb/llm.py:116`; `kb/vlm/litellm.py:43`; `kb/embedder.py:212` |
| Dotted-path loading | `factories.py:1431-1458` |
| Superuser gate (403) | `api/tool_usage_routes.py:143-145` |
| Superuser nav entries | `web/src/hooks/usePlatformDestinations.ts` (the `isSuperuser` block) |
| ECharts guard | `web/src/ext/imports.test.ts:292-350` |
| API image installs no extras | `docker/Dockerfile:81` |
| Work calendar | `workcalendar.py:79` |

## 18. Sources

- Spinner, Casale, Brosig, Kounev — [Evaluating approaches to resource demand estimation](https://spiral.imperial.ac.uk/entities/publication/47deb39e-4647-4fc8-b622-442cc571c038), Performance Evaluation, 2015.
- Jain — [Operational laws](https://classes.engineering.wustl.edu/~jain/cse567-15/ftp/k_33ol.pdf) (the utilization law).
- Lawson & Hanson — *Solving Least Squares Problems*, 1974 (non-negative least squares).
- Muggeo — Estimating regression models with unknown break-points, *Statistics in Medicine* 22 (2003).
- Davison & Hinkley — *Bootstrap Methods and their Application*, 1997 (resampling hierarchical data).
- [Bootstrapping technique to predict critical care capacity requirements](https://pmc.ncbi.nlm.nih.gov/articles/PMC10538684/).
- Lewis — *Industrial and Business Forecasting Methods*, 1982 (MAPE bands).
- Google — [SRE book, introduction: demand forecasting and capacity planning](https://sre.google/sre-book/introduction/).
- Kubecost — [Allocations](https://docs.kubecost.com/using-kubecost/navigating-the-kubecost-ui/cost-allocation); OpenCost — [Specification](https://www.opencost.io/docs/specification).
- LiteLLM — [Request tags](https://docs.litellm.ai/docs/proxy/request_tags), [request headers](https://docs.litellm.ai/docs/proxy/request_headers), [tag routing](https://docs.litellm.ai/docs/proxy/tag_routing).
- NVIDIA — [nvidia-smi](https://docs.nvidia.com/deploy/nvidia-smi/index.html) (`utilization.gpu`).
- Linux — [cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html), [PSI](https://docs.kernel.org/accounting/psi.html).

## 19. As built (#962)

What the build changed from the plan above, each for a reason found while building it:

- **The request counter sits inside the perf trace**, not outermost: `PerfTraceMiddleware` stays
  outermost for the reason its comment gives (its wall clock must include every middleware).
- **The turn's person is `agent.tool_usage.who`** (usage_user, else speaker, else acting user) — the
  rule the tool-usage statistics count by, so the two pages cannot disagree; `NOBODY` maps to `system`.
- **No `ws.component` tag**: the purpose already says where (a turn surface or a job type). And a call
  outside any usage scope carries **no tags** rather than `ws.user:system`; tags are only sent while
  recording is on (`capacity.record`), because only a recorder opens scopes.
- **The failover producer thread now runs in its caller's context** (`failover/core.py`): a bare thread
  started with an empty context and dropped the tags of every KB call behind a failover chain. Found by
  the tag test that calls each adapter the way production does.
- **Collinear columns are reported, not guessed apart**: a chat send and its turn's events always come
  together, so only their sum is identifiable; view 9 says so (`FitStats.collinear`).
- **The knee search reaches the 99th percentile** (not the 95th) — knees live in peak hours — and a knee
  must survive losing the 30 highest-load points, so a break drawn by a few far points is rejected.
- **(d) uses the sandbox time-weighted wait share** (Σ stalled ÷ Σ alive per slot), not a p95 across
  sandboxes: it needs no per-sandbox rows from ①.
- **A model's GPU capacity is its total in-flight level at the knee** with its current cards; one more
  card adds knee ÷ cards (data-parallel replicas). The headroom ignores lines N cannot push: fixed lines
  and disk (disk runs out over time, view 7).
- **"N people" reads 30 days before the period** (one grouped ① query by person and UTC day), so the
  first day's N is as complete as the last's.
- **The 90-day period is analysed at 15-minute slots** or coarser, to bound memory.
- **`turn` records are stamped at acceptance, `job` records at start**, so a period selects work by when
  it began.
- **`kind: local` with isolation**: the API pod's own sandboxes are subtracted from its CPU and memory
  before the API regression, since they run inside it.
- **The ECharts guard** (`web/src/ext/imports.test.ts`) holds a door rule: only `capacity/echarts.ts`
  imports ECharts, every other module reaches it with `import()` or `import type`, and web's range is the
  chart plugin's. The production build puts ECharts in its own chunk (577,423 bytes minified, measured
  on the #962 build); the entry chunk keeps only the `import("./echarts-….js")` call.
- **The middleware's cost**: `scripts/bench_usage_middleware.py` measured 12.6 µs per request on the dev
  box (47.5 → 60.1 µs on a trivial route, median of 5 rounds × 10,000).

