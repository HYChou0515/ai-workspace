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
| What is not consumed? | **Logged at boot**, one line: `lifespan: run_consumers=[…]; NOT consumed on this process: chat-video, graph (their jobs stay pending until a worker takes them)`. |
| Env form? | `${RUN_CONSUMERS}` must work: `true` / `false` (case-insensitive) → bool, anything else → comma-separated list (`index,card-gen`, whitespace tolerated). This is also the fix for the string-`'false'` defect. |
| Validation of the names against what? | `worker._JOBTYPE_ATTR` (`worker/__init__.py:30-47`) — the one table `python -m workspace_app.worker <jobtype>` accepts, guarded by `tests/deploy/test_worker_manifests.py` against the k8s manifests. Not a second list. |
| Shutdown? | A coordinator this process did not consume is not `aclose`d — the pure-producer rule (`lifecycle.py:680-690`) applied per coordinator instead of per process. |
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
  `ValueError("server.run_consumers: unknown JobType 'chat_video' — valid: index, wiki, card-gen, sanity, eval, graph, kb-import, blob-gc, chat-video")`
  — raised from `_validate` (`loader.py:315`), so it lands beside the
  unknown-key refusals and reads the same way at boot.
- An empty list is `false` (nothing consumed); the log says so.
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

The shutdown loop (`lifecycle.py:690-700`) iterates the same table and skips
what was not selected — which also makes it cover `kb_import`, absent from the
hand-written tuple today (a pre-existing gap: an in-flight archive import is
not drained at shutdown; observed, and closed by construction here).

The per-coordinator `None` checks (`sanity`, `eval`, `graph` may be unwired)
survive as the `getattr(..., None)` — an unwired coordinator is neither started
nor reported as "not consumed" (it is not a choice the operator made).

### What "not consumed" looks like to a caller

Unchanged from the pure-producer shape: the route enqueues, the job row sits
`pending`, nothing errors. The boot log line is the only place that says why —
which is why it is mandatory, not `debug`.

## Deliberately not doing

- **No deny-list.** Decided.
- **No per-JobType concurrency or ordering** — the list says which, nothing more.
- **No runtime toggle.** The selection is read once at boot like the bool was.
- **No change to the worker CLI or the k8s worker Deployments.**
- **No coercion for other `bool` fields fed by `${VAR}`.** The same defect class
  may exist elsewhere (`_build` coerces nothing); this plan fixes the one key it
  is about and NAMES the class in the migrations entry. A sweep is its own PR —
  `git grep -n '\${' configs/config.example.yaml` lists the candidates.

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
  `True` case asserts all six.
- The boot log (`caplog`) carries `NOT consumed on this process: blob-gc,
  chat-video, kb-import, wiki` for that list, and no such line for `True`.
- `[]` behaves as `False` (nothing consuming, line says all nine).
- Shutdown: with the list, `aclose` is called on the two consumed coordinators
  and NOT on the others (spy on `aclose`); with `False`, on none — the existing
  pure-producer assertion, kept.
- Mutation: delete the `jobtype not in selected` check → the list case
  reddens; hard-code the old nine-block start → `kb_import` shutdown coverage
  reddens.

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
  `:690-700` shutdown `aclose` loop over a hand-written tuple of eight names —
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
  `.consuming` on six coordinators for `True` and `False`.
- `kubernetes/base/configmap.yaml:16-19` — the `${RUN_CONSUMERS}` mapping
  advice + `RUN_CONSUMERS: "false"`; `configs/config.example.yaml:63-69`;
  `docs/configuration.md:81,115,140` and §8 (`:433-452`); `docs/deployment.md`
  §11 (`:461-491`), §13 `RUN_CONSUMERS=true/false` (`:1248-1250`).
