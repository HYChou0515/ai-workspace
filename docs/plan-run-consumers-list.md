# Plan — `server.run_consumers` takes a list: an all-in-one process that consumes SOME JobTypes

## Problem

`server.run_consumers` is a `bool` (`config/schema.py:51`): `true` starts every
in-process consumer, `false` starts none. There is nothing between. A single
machine that wants everything except the two heavy kinds — `chat-video` needs
Chromium + ffmpeg on the box, `graph` / `eval` burn model calls on a schedule —
has no way to say so short of running eight worker processes against a shared
Postgres, which is the pod-split shape and not what "single machine" means.

There is a second, pre-existing defect on the same key. The loader expands a
`${VAR}` marker to the env's **string** and builds the dataclass with
`cls(**sub)` (`config/loader.py:1034`, `_build`), with no coercion to the
field's type. So the mapping every deployment doc recommends —
`server: { run_consumers: ${RUN_CONSUMERS} }` with the configmap's
`RUN_CONSUMERS: "false"` (`kubernetes/base/configmap.yaml:16-19`,
`config.example.yaml:69`, `docs/deployment.md` §13) — yields the string
`'false'`, which `lifecycle.py:530` tests with `if run_consumers:`, and the
"pure producer" API pod consumes every JobType after all. Probed on
`0285e952`: `load(config_path=…, env={"RUN_CONSUMERS": "false"})` →
`server.run_consumers == 'false'`, `bool(...) is True`. The same probe shows a
YAML list already passes the loader untyped (`['index', 'card-gen']`) and is
then truthy — "all", silently. Whether production is bitten depends on whether
its `config.yaml` writes a literal `false` or the marker, which this repo
cannot see; the repo's own manifests teach the marker.

## Locked decisions (from the chat, 2026-09-21)

| Question | Decision |
|---|---|
| One key or two? | **One key, three shapes.** `run_consumers: true` (all, the default), `false` (none — pure producer), `[index, card-gen]` (only these). No second `skip_consumers` / `consumers` key: two keys that both decide the same thing drift apart. |
| Allow-list or deny-list? | **Allow-list** (the list names what IS consumed). Chosen by the user over the deny-list I proposed. Its one cost — a JobType added later is not consumed until listed — is made loud, not silent (below). |
| Unknown names? | **Refuse at boot**, naming the field, the bad name and the valid names — the same strictness the loader already applies to unknown keys. A typo must not become "that queue never moves". |
| What is not consumed? | **Printed at boot**, on stdout beside the `→ start <jobtype> consumer …` steps: `⚠ consumers: NOT consumed on this process: chat-video, graph, … (their jobs stay pending until a worker takes them)` — names sorted. *(Round 1 correction: the plan said "logged"; a `logger.info` reaches no real pod's log, see below.)* A name the operator LISTED that this deployment never wired gets its own line, `⚠ consumers: listed in run_consumers but not wired on this deployment: graph (nothing to start)`. |
| Env form? | `${RUN_CONSUMERS}` must work: `true` / `false` (case-insensitive) → bool, anything else → comma-separated list (`index,card-gen`, whitespace tolerated). This is also the fix for the string-`'false'` defect. |
| Validation of the names against what? | `worker._JOBTYPE_ATTR` (`worker/__init__.py:30-47`) — the one table `python -m workspace_app.worker <jobtype>` accepts, guarded by `tests/deploy/test_worker_manifests.py` against the k8s manifests. Not a second list. |
| Shutdown? | A coordinator this process did not consume is not `aclose`d — the pure-producer rule (`lifecycle.py:682-690`) applied per coordinator instead of per process. |
| Worker CLI? | Untouched. `python -m workspace_app.worker <jobtype>` consumes that JobType whatever the API's setting. |

## Design

### The value — parsed once, at the loader

`ServerSettings.run_consumers: bool | list[str] = True` (schema). A new
loader step, applied to `server.run_consumers` after env expansion and before
`_build`:

```python
def consumer_selection(raw: object) -> bool | list[str]:
    """`true` / `false` / a list of JobType names — from YAML (bool or list)
    or from a `${VAR}` string (`"true"`, `"false"`, `"index,card-gen"`)."""
```

- `bool` → as is. `str`: `"true"` / `"false"` (case-insensitive, stripped) →
  bool; otherwise split on `,`, strip, drop empties → list. `list` → every
  element must be a `str`.
- Every name in a list must be a key of `worker._JOBTYPE_ATTR`; otherwise
  `ValueError("server.run_consumers: unknown JobType 'chat_video' (<path>) — valid: index, wiki, card-gen, sanity, eval, graph, kb-import, blob-gc, chat-video; or true / false for all / none")`
  — raised from loader step **5b**, right after `_validate` (`loader.py:315`)
  and before `_settings_from_dict`, so it lands beside the unknown-key
  refusals and reads the same way at boot. *(As built: a separate step, not
  inside `_validate` — `_validate` checks structure and `consumer_selection`
  also REPLACES the value.)*
- An empty list is `false` (nothing consumed); the printed line names every
  wired JobType. `","` from the env (separators, no names) is refused like
  `""`, not read as `[]`.
- Anything else is refused at load: YAML `null` (a bare `run_consumers:`),
  `0` / `1`, `""`, a mapping; from the env, any string that is not
  `true` / `false` / JobType names (`no`, `0`, `1`). Measured on the branch
  (`shapes_probe.py`): every one of these LOADED before — `null` and `0`
  falsy (a pure producer by accident), `1` and every env string truthy —
  so this is "refuses to boot" for the runbook. YAML `yes` / `no` / `on` /
  `off` are PyYAML booleans and load as before.
- `worker/__init__.py` imports nothing from `config` at runtime — its only
  module-level imports are `asyncio` and `threading`; `CoordinatorBundle` is
  under `TYPE_CHECKING` — so the loader importing `_JOBTYPE_ATTR` is not a cycle.

The k8s configmap comment and `config.example.yaml:63-69` change to show all
three forms and the env spelling.

### The gate — per JobType, from the one table

`lifecycle.py:530-564` is nine hand-written `start_consuming()` blocks under one
`if run_consumers:`. It becomes:

```python
selected = consumer_set(run_consumers)   # frozenset of JobType names
for jobtype, attr in _JOBTYPE_ATTR.items():
    coordinator = getattr(app.state, f"{attr}_coordinator", None)
    if coordinator is None or jobtype not in selected:
        continue
    with boot_step(f"start {jobtype} consumer"):
        coordinator.start_consuming()
```

with `consumer_set(True) = all keys`, `consumer_set(False) = ∅`,
`consumer_set(list) = set(list)`; and the boot line naming
`sorted(all - selected)` when non-empty. The `app.state` attribute is spelled
from the table (`f"{attr}_coordinator"`), which requires ONE rename:
`app.state.import_coordinator` → `app.state.kb_import_coordinator`
(`app.py:1724`, `lifecycle.py:556`, `tests/api/test_consumer_gate.py:41,71`;
the `import_coordinator=` kwarg of `register_kb_routes` at `app.py:1805` is a
parameter name, not state, and stays). Every other attribute already matches.

The shutdown loop (`lifecycle.py:696-705` on master) iterates the same table and skips
what was not selected — which also makes it cover `kb_import`, absent from the
hand-written tuple today (a pre-existing gap: an in-flight archive import is
not drained at shutdown; observed, and closed by construction here).

The per-coordinator `None` checks (`sanity`, `eval`, `graph` may be unwired)
survive as the `getattr(..., None)` — an unwired coordinator is neither started
nor reported as "not consumed" (it is not a choice the operator made) — but
one the operator LISTED (`[index, graph]` on a deployment with no KB LLM) is
said, on its own line, or the list silently starts nothing for a name they
wrote (round 1).

### What "not consumed" looks like to a caller

Unchanged from the pure-producer shape: the route enqueues, the job row sits
`pending`, nothing errors. The printed boot line is the only place that says
why — which is why it is mandatory, and PRINTED: this app configures Python
logging nowhere (`api/perf_trace.py:193-206` records the trap), so
`logger.info` emits nothing in a real `python -m workspace_app` boot. The first
version of the line was a `logger.info`, the tests saw it through `caplog`,
and the regression lens booted the image five times and grepped 0 hits. The
printed line is the same channel as `boot_step` and the `⚠ resources:`
warning (`lifecycle.py:535`), and the tests read it with `capsys`.

## Deliberately not doing

- **No deny-list.** Decided.
- **No per-JobType concurrency or ordering** — the list says which, nothing more.
- **No runtime toggle.** The selection is read once at boot like the bool was.
- **No change to the worker CLI or the k8s worker Deployments.**
- **No coercion for other `bool` / `int` fields fed by `${VAR}`.** The same
  defect class exists elsewhere (`_build` coerces nothing); this plan fixes the
  one key it is about. A sweep is its own PR. The candidates are NOT in
  `config.example.yaml` (its `${VAR}` markers are all secrets / URLs — strings);
  they are the non-string values `kubernetes/base/configmap.yaml` defines for
  an operator to map: `grep -n '"\(true\|false\|[0-9]\+\)"' kubernetes/base/configmap.yaml`
  → `APP_PORT: "8000"` (`server.port: int`), `SANDBOX_ISOLATE: "false"`
  (`sandbox.isolate: bool | None`), `SANDBOX_ISOLATION_ENABLED: "false"`
  (`sandbox.isolation.enabled: bool | None` — the configmap's own comment at
  `:41` (master `:38`) teaches mapping it; `factories.py:314` takes the value as is and `:331`
  `elif want_uid_isolation:` is truthy for the string `"false"`, so it reads
  as the explicit opt-in: uid isolation ON, or a boot failure on a host that
  cannot isolate — read, not yet probed),
  `SANDBOX_HTTP_HOST_MANAGED_DURABLE` (`sandbox.http.host_managed_durable:
  bool`, `:56/:69`). The grep also lists `KB_EMBED_DIM` (`resources/kb.py:99`),
  `LITELLM_*` and `WORKSPACE_PERF_TRACE*`, which the app reads from the env
  directly, not through `config.yaml` — not candidates. Whether each is bitten depends on
  how its reader tests the value; the sweep verifies each through the real
  loader before touching it. Also out of scope, same file: the `:41` flow-form
  comment `sandbox: { isolation: { enabled: ${…} } }` is a YAML parse error
  (this PR fixed only the `:16` one it was already editing).
- **Not fixed, pre-existing:** a bare `server:` (YAML `null`) crashes the
  loader on master (`TypeError` in `_build`) and on this branch
  (`AttributeError` at step 5b) — equally unhelpful, out of scope.

## Phases (one commit each)

1. **Parse** — `consumer_selection` in `config/loader.py` (+ the schema type),
   applied to `server.run_consumers`; unknown names refused in `_validate`;
   `test_config_run_consumers.py` (RED first, incl. the env-string case that
   reddens on today's loader).
2. **Gate** — `lifecycle.py` start + shutdown loops from `_JOBTYPE_ATTR`; the
   `kb_import_coordinator` rename; the boot line; `test_consumer_gate.py`
   grows the list case.
3. **Docs** — `docs/configuration.md` (§3 row + §8 paragraph: three shapes, env
   spelling, "not listed = pending, said at boot"), `configs/config.example.yaml`,
   `kubernetes/base/configmap.yaml` comment, `docs/deployment.md` §11 one
   sentence, and the **`docs/migrations.md` entry** (below).
4. **Review round 1 fixes** — the printed line (+ sorted, + the listed-but-
   unwired line), provenance for the env-built list and the empty list,
   `","` refused, the refusal message offering `true / false`, the
   `kb_import` drain pinned, the configmap flow-form comment, and every doc
   sentence rewritten from a real boot log (below).

## Test plan (red first, targeted only)

Phase 1 (`tests/config/test_run_consumers.py`, through the real `load`):

- YAML `true` / `false` → `True` / `False`; YAML `[index, card-gen]` →
  `["index", "card-gen"]`.
- `${RUN_CONSUMERS}` = `"false"` → `False` (**reddens on today's loader**:
  it yields `'false'`); `"TRUE"` → `True`; `"index, card-gen"` → the list;
  `""` → `ValueError` naming the field.
- `[index, chat_video]` → `ValueError` whose message contains `chat_video`
  and every valid name; `[index, 3]` → `ValueError`.
- Default (no `server:` block) → `True`.

Phase 2 (`tests/api/test_consumer_gate.py`, real `create_app` + `LifespanManager`):

- `run_consumers=["index", "card-gen"]` → `index_coordinator.consuming` and
  `card_gen_coordinator.consuming` are true; `wiki`, `kb_import`, `blob_gc`,
  `chat_video` are false; `enqueue` on a non-consumed one still returns `True`
  (the job is left for a worker). Positive control beside it: the existing
  `True` case asserts five.
- Stdout (`capsys`, not `caplog`) carries exactly `  ⚠ consumers: NOT consumed
  on this process: blob-gc, chat-video, kb-import, wiki (their jobs stay pending
  until a worker takes them)` for that list (sorted — table order would be
  `wiki, kb-import, blob-gc, chat-video`), and no `⚠ consumers:` line for
  `True`; `[index, graph]` on the test app (no `graph_coordinator`) carries
  the `listed … but not wired` line and no `graph` in the NOT-consumed one.
- `[]` behaves as `False` (nothing consuming, line says all nine).
- Shutdown: with the list, `aclose` is called on the two consumed coordinators
  and NOT on the others (spy on `aclose`); with `False`, on none — the existing
  pure-producer assertion, kept.
- Mutation: delete the `jobtype not in selected` check → the list case
  reddens; `or jobtype == "kb-import"` on the DRAIN loop's check → the `True`
  case reddens on its `assert not kb_import_coordinator.consuming` after the
  lifespan (round 1: this line was missing, and that mutation was 6/6 green).
- Provenance (`tests/config/test_run_consumers.py`): `${RUN_CONSUMERS}` =
  `index,card-gen` → `server.run_consumers[0]` / `[1]` are `env` with
  `ref == "${RUN_CONSUMERS}"` (was `default`: the list has no operator path
  of its own — it takes the parent scalar's, `_parent_list_source`); YAML
  `[]` → `server.run_consumers` is `config.yaml` (was `default`: an empty list
  recorded no source — pre-existing, `superusers: []` did the same).

Phase 3: `mkdocs build --strict` green; `tests/deploy/test_worker_manifests.py`
untouched and green (the table did not move).

## `docs/migrations.md` entry (what the operator must do)

Required because behaviour changes with no knob: a deployment that maps
`run_consumers: ${RUN_CONSUMERS}` to `"false"` has been consuming on its API
pods; after this PR those pods are the pure producers the docs said they were,
so **the worker Deployments must actually be running** (`kubectl get deploy |
grep rca-worker-`; the base `workers.yaml` has one per JobType). Symptom of
skipping it: help docs stay `indexing`, uploads sit `pending`, blob GC never
runs. WHEN: before the rollout (workers up first — they are idempotent
consumers of a durable queue, so running them beside the old pods is safe).
Nothing to migrate in the store.

## Verified ground truth (file pointers, origin/master `0285e952`)

- `config/schema.py:51` — `run_consumers: bool = True` on `ServerSettings`.
- `config/loader.py:99` `load` → `:110` `load_with_provenance`; env expansion
  `_walk_strings(raw_yaml, lambda s: expand_env(s, e))` (`:132-133`);
  `_validate` at `:315` (`_check_unknown_keys` …); `_settings_from_dict` →
  `server=_build(ServerSettings, d["server"])` at `:969`; `_build` is
  `cls(**sub)` at `:1034-1038` — no coercion. `expand_env` is
  `config/interpolate.py:51`, returns `str`.
- Probe (this branch, `/home/hychou/.claude/jobs/a936d38a/tmp/probe_run_consumers.py`):
  env `"false"` → `str 'false' truthy: True`; YAML list → `['index', 'card-gen']`.
- `api/lifecycle.py:88` `run_consumers: bool` param; `:529-530` the log + gate;
  `:531-564` nine `start_consuming()` blocks (`wiki`, `index`, `sanity`?,
  `eval`?, `graph`?, `card_gen`, `import`, `blob_gc`, `chat_video`);
  `:696-705` shutdown `aclose` loop over a hand-written tuple of eight names —
  `import_coordinator` absent.
- `api/app.py:447` `run_consumers: bool = True` on `create_app`; `:1327` passed
  to `build_lifespan`; `app.state.*_coordinator` assignments at `:1714-1797`,
  `app.state.import_coordinator = coordinators.kb_import` at `:1724`.
- `worker/__init__.py:30-47` `_JOBTYPE_ATTR` — nine keys: `index`, `wiki`,
  `card-gen`, `sanity`, `eval`, `graph`, `kb-import`, `blob-gc`, `chat-video`;
  values are the `CoordinatorBundle` field names (`coordinators.py:58-83`).
- `tests/deploy/test_worker_manifests.py:41,49` — the table ↔ `workers.yaml`
  guard, both directions.
- `tests/api/test_consumer_gate.py` — `_app(run_consumers=…)`, asserts
  `.consuming` on five coordinators for `True` and for `False` (`index`,
  `wiki`, `card_gen`, `import`, `chat_video`; `sanity` / `eval` / `graph`
  are `None` in that composition).
- `kubernetes/base/configmap.yaml:16-19` — the `${RUN_CONSUMERS}` mapping
  advice + `RUN_CONSUMERS: "false"`; `configs/config.example.yaml:63-69`;
  `docs/configuration.md:81,115,140` and §8 (`:433-452`); `docs/deployment.md`
  §11 (`:461-491`), §13 `RUN_CONSUMERS=true/false` (`:1268-1269`, `:1317`).

## Review round 1 (2026-09-21, four lenses in parallel on `1d8a620c`, P1–P3)

Conformance / veracity / defect / regression, each in its own worktree from
`origin/master`, `git checkout --detach 1d8a620c`. The regression lens also
booted the real app five times (old/new × true/false, new × list) and ran 46
config inputs through both loaders — the only leaf that differed anywhere was
`server.run_consumers`.

| # | Finding | Lens | Fix (P4) |
|---|---|---|---|
| 1 | **HIGH** — `logger.info("lifespan: NOT consumed …")` never appears in a real boot (no logging config; 0 hits in five boot logs). Runbook "確認做完" greps and three doc sentences described a line that did not exist. | all four | `print(f"  ⚠ consumers: {sentence}", flush=True)` beside the `⚠ resources:` precedent; logger line kept; tests read `capsys`; every doc sentence rewritten from the real log (`p4-list.log:676-680`). |
| 2 | Config dump labelled the env-built list `- index  # ← default` (elements have no operator path; the scalar had `env`). `[]` recorded no source (pre-existing class, `superusers: []` too). | veracity, regression | `_parent_list_source`: an element with no operator path takes its parent list's source; an empty list records `config.yaml`. Real boot now prints `- index  # ← env`. |
| 3 | `kb_import` drain not pinned — mutation `or jobtype == "kb-import"` on the drain check was 6/6 green. | defect, regression | `assert not kb_import_coordinator.consuming` after the lifespan in the `True` case; mutation now reddens exactly it. |
| 4 | No test pinned "no line for `True`"; boot line was table order, plan said sorted. | veracity | `_said(capsys) == []` in the `True` case; `sorted(skipped)`, exact-string assertion (table order would redden). |
| 5 | A listed-but-unwired JobType (`[graph]` without a KB LLM) silently started nothing. | defect, regression | Second printed line, `listed in run_consumers but not wired on this deployment: graph (nothing to start)`; test on the test app's `None` `graph_coordinator`. |
| 6 | `RUN_CONSUMERS=no` / `0` refused as "unknown JobType" without offering `true`/`false`; `","` → `[]` while `""` was refused. | conformance, regression | Message ends `; or true / false for all / none`; `","` refused as "no JobType names". |
| 7 | Behaviour changes not in the runbook: YAML `null` / bare / `0` / `1` / `""` / mapping and env `no` / `0` / `1` went from silently loaded (null and 0 = pure producer!) to refused. | regression | Runbook bullet with the measured before/after and the CrashLoop symptom (`ValueError: server.run_consumers: …` as the last line; load runs before the `config:` print). |
| 8 | `configmap.yaml:16` flow form `server: { run_consumers: ${RUN_CONSUMERS} }` is a YAML parse error on both checkouts (`{` inside a flow mapping) — verified with `yaml.safe_load`. | regression | Comment rewritten to block form, with the reason. |
| 9 | Plan claims: refusal "from `_validate`" (it is step 5b after it); "one line" (two: `run_consumers=` and `NOT consumed`); "six coordinators" (five); `deployment.md` §13 at `:1248-1250` (`:1268-1269`, `:1317`); lifecycle tuple `:690-700` (`:696-705`). | veracity | Corrected above, each re-counted on `0285e952`. |
| 10 | Same defect class confirmed elsewhere: `sandbox.isolation.enabled: ${SANDBOX_ISOLATION_ENABLED}` = `"false"` reads as `True` (fail-loud / isolation on). Plan's suggested grep (`config.example.yaml`) finds only strings. | defect | Out of scope; named with the right grep and the concrete candidates under "Deliberately not doing"; ticket to open. |
| 11 | Runbook `grep -c rca-worker- ≥ 2` did not match its own symptoms (kb-import / blob-gc). | conformance | Symptoms mapped to their JobType and worker; the check lists the workers you need. |

Mutation probes for P4 (`mutate_p4.py`, file-copy restore, `__pycache__` wiped):
unsorted → list case; logged-not-printed → list + empty-list cases; unwired not
collected → unwired case; `kb-import` skipped at drain → `True` case; empty list
records no source → its test; env-list elements fall back to default → its
test. Six of six reddened exactly the claimed test; restore verified (23 green,
`git diff --stat` = the four intended files).

Round 1 fixes replace no mechanism (the gate loop, the loader step and the
provenance walk are the same; the sentence moved from a logger to stdout and
two rules were added to the provenance walk with a test each), so P4 gets a
verify-the-fix pass, not a fourth lens round.
