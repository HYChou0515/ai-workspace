# Job tool template

A tool for work that runs somewhere else and is asked about later: GPU
training, batch reports, an approval flow. You write the part that talks to
your backend; this template is everything else — the commands the model sees,
how long each call may take, and what the model is told after every outcome.

## Three packages

```
contract/       job-contract — the interface (JobBackend) and what a backend declares
template/       THIS — the tool itself. Copy it as your tool repository.
example-impl/   example-jobs — a DEMO backend. Copy it as the start of yours.
```

Dependencies run one way: your backend depends on `job-contract`; this tool
depends on `job-contract` and your backend. So changing the backend is a new
version of the backend package, and upgrading the template never touches it.

## Start

1. Copy `template/` as your tool repository. Rename `job-tool` in
   `pyproject.toml` (the project name and the one `[project.scripts]` key).
2. Copy `example-impl/` as your backend package. Rename it, keep the
   `[project.entry-points."job_tool.backend"]` line pointing at your class.
3. In this repository's `pyproject.toml`, replace `example-jobs` with your
   backend, and point `[tool.uv.sources]` at git URLs or an index — the builder
   mounts only this repository, so `../` paths do not exist there. (While they
   are still `../` paths, a local build reuses uv's cached wheel for an
   unchanged version number: bump the version, or
   `uv cache clean job-contract <your-backend>`, after editing either.)
4. `uv lock`, then `uv run python -m job_tool.envfile` to write `env.json`.
5. `uv run pytest`, then push. CI runs the same tests before building.

## What your backend declares, once

On its `Contract`, read from there by the shell, the shape tests and the
description the model reads — never restated anywhere else:

| field | what it is |
|---|---|
| `spec_schema` | JSON schema of what `submit` accepts. The model fills these fields directly. Arguments are checked against it before your backend is called, so add `"additionalProperties": false` if fields you did not declare must not reach you. |
| `idempotency` | `fields`: which spec fields make two submits the same job — **per item**: the same work from two items is two jobs. Within one item it is also per **credential**, because your backend can only find jobs its token sees: two people in one item with their own Private tokens get two jobs. `window_seconds` (default 86400, a day; positive and finite): how long it stays the same job; after it the same work starts a new one. Override `identity(spec)` to compare loosely (case, spaces, nested fields) — then also give `example_spec`, on which the shape tests check it gives the same answer twice in one process and in twenty processes with different hash seeds (no set order, `hash()` or `id()` in it). The shell derives the key; you never do. |
| `budget` | seconds per call. Defaults: submit 10, find_by_key 5, get 3, list 5, update 10, cancel 10, logs 5, fetch 50. Each at most 55, and submit + find_by_key at most 55 together. |
| `patch_schema` | what `update` accepts, or None when you have no `update`. Must come with `update`, and `update` with it. |
| `domain_description` | what these jobs ARE, in your words. The model reads it first. |
| `env` | the variables you read. None = "did not say", which is not `()` = "needs none". |

A contract value that cannot hold (a budget past the ceiling, sameness named on
a field the spec does not have) is refused when it is written. A backend that
leaves out a required call, or has `update` without `patch_schema` (or the
reverse), is refused when it is loaded — which the shape tests do, so CI fails
before anything is built. A base class that only shares helpers between
backends may be incomplete; only the class you register must be whole.

## What your backend implements

Required: `submit`, `find_by_key`, `get`, `list`. Optional:
`update`, `cancel`, `logs`, `fetch` — one you do not write is one the model is
never offered. The docstrings in `contract/src/job_contract/backend.py` say
what each must do; the ones that matter most:

- **`submit` with a key it has seen within the window returns that job, and
  says so:** `Submitted(job_id, created=False)`; a job it starts is
  `created=True`. Nothing tests this (the shape tests never call your
  service); it is the one promise only you can keep, and breaking it is two
  jobs and two bills.
- **`submit` is given the item id; record it and return it as
  `JobStatus.item_id`.** The shell uses it to tell the model which jobs came
  from this item. It is a label, not a permission.
- **Who may see or change a job is your credential's business.** `get` and
  `list` answer with what the token may see — the whole queue, not this
  item's slice; the model is told which are this item's. A token in the
  item's environment variables is shared by everyone who can open the item; a
  token in a person's Private layer is theirs alone. Items that must not see
  each other's jobs need different tokens.
- **`get` that cannot ask returns `phase="unknown"`**; `NotFound` is only for
  a job you are certain does not exist.
- **`phase` is one of `running`, `done`, `unknown`.** Your own word for where a
  job is ("suspended", "failed: OOM") goes in `state`.

Raise `Retryable` when calling again may work, `NeedsAction` when a person
must act first — naming the variable in full (`set GPU_API_TOKEN`), which is
what lets the platform offer them a way to set it — and anything else for a
plain failure.

## What the model is told

| outcome | exit | the model hears |
|---|---|---|
| a call returns | 0 | the answer, and a `next` line: ask again later / finished, its `state` says how (and, if offered, `logs`, or `fetch` if it produced outputs) / read on from offset N. Every job carries `from_this_item` |
| `submit` finds the same work already there (within the window) | 0 | `created: false` — "this item already submitted the same work within the last 24 hours: this is that job, not a new one" |
| `get` returns `phase="unknown"` | 2 | the backend could not say — not that the job is gone (a `cancel` that returns it succeeded: 0, "Cancel accepted." followed by the same words) |
| a call overruns its budget | 2 | no answer within N s; calling again may work |
| a submit overruns before it was sent (building your backend) | 2 | most likely not sent; sending the same spec again is safe (same key) |
| a submit overruns, and `find_by_key` finds it | 0 | the job is there (`created: null` — the lookup cannot say whether this call started it) |
| a submit overruns, and `find_by_key` finds nothing | 2 | not there; sending the same spec again is safe |
| a submit overruns, and `find_by_key` cannot answer | 1 (3 if it raised `NeedsAction`) | unknown; call `list` before submitting again |
| arguments outside the schema, or more than one argument | 2 | what is wrong; the backend is never called |
| `fetch` to a path that is empty, absolute, through `..`, or out through a link | 2 | it must name a file inside the workspace |
| your code raises anything else, or calls `sys.exit`, or `submit` returns a bare id instead of `Submitted` | 1 | the backend failed, and with what |

Nothing announces when a job ends: the model is told to have the user ask
again later.

Three more things the shell does so your backend does not have to:

- **stdout is only the answer.** From the moment your backend is imported
  until the process exits, anything written to stdout — your `print`, a
  library's banner, a command you run such as `sbatch`, work you leave running
  that speaks at exit — goes to stderr instead; the answer is written to the
  real stdout on its own. (The command-level part needs stdout to be a real
  file descriptor, which it is under the platform's launcher and the MCP
  runner; elsewhere only Python's `print` is redirected.) Values JSON cannot spell (a datetime in `detail`,
  NaN) are written as text rather than failing a call that succeeded.
- **Whole numbers arrive as `int`.** JSON Schema counts `5.0` as an integer;
  the shell turns it into `5` before your code or the idempotency key sees it.
- **A call that overruns does not keep the process alive.** Once a call is
  abandoned, the process ends as soon as the answer is out, without waiting
  for threads your code started (a client library's pool, say). A hung
  `submit` therefore costs its budget plus `find_by_key`'s — at most 55 s.
