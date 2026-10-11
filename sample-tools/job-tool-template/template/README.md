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
   mounts only this repository, so `../` paths do not exist there.
4. `uv lock`, then `python -m job_tool.envfile` to write `env.json`.
5. `uv run pytest`, then push. CI runs the same tests before building.

## What your backend declares, once

On its `Contract`, read from there by the shell, the shape tests and the
description the model reads — never restated anywhere else:

| field | what it is |
|---|---|
| `spec_schema` | JSON schema of what `submit` accepts. The model fills these fields directly. |
| `idempotency` | `fields`: which spec fields make two submits the same job. `per_user` (default true): two callers are two jobs. The shell derives the key; you never do. |
| `budget` | seconds per call. Defaults: submit 10, find_by_key 5, get 3, list 5, update 10, cancel 10, logs 5, fetch 50. Each at most 55, and submit + find_by_key at most 55 together. |
| `patch_schema` | what `update` accepts, or None when you have no `update`. Must come with `update`, and `update` with it. |
| `domain_description` | what these jobs ARE, in your words. The model reads it first. |
| `env` | the variables you read. None = "did not say", which is not `()` = "needs none". |

A declaration that cannot hold is refused when the class is defined, not at
the first call.

## What your backend implements

Required: `caller_id`, `submit`, `find_by_key`, `get`, `list`. Optional:
`update`, `cancel`, `logs`, `fetch` — one you do not write is one the model is
never offered. The docstrings in `contract/src/job_contract/backend.py` say
what each must do; the ones that matter most:

- **`submit` with a key it has seen returns the same job.** Nothing tests this
  (the shape tests never call your service); it is the one promise that only
  you can keep, and breaking it is two jobs and two bills.
- **`get` that cannot ask returns `phase="unknown"`**; `NotFound` is only for
  a job you are certain does not exist.
- **`phase` is one of `running`, `done`, `unknown`.** Your own word for where a
  job is ("suspended", "failed: OOM") goes in `state`.
- **`caller_id` is who the credential belongs to.** Two people sharing one
  token are one caller: they see each other's jobs, and the same spec from
  either is one job.

Raise `Retryable` when calling again may work, `NeedsAction` when a person
must act first — naming the variable in full (`set GPU_API_TOKEN`), which is
what lets the platform offer them a way to set it — and anything else for a
plain failure.

## What the model is told

| outcome | exit | the model hears |
|---|---|---|
| a call returns | 0 | the answer, and a `next` line: ask again later / it finished / read on from offset N |
| `phase="unknown"` | 2 | the backend could not say — not that the job is gone |
| a call overruns its budget | 2 | no answer within N s; calling again may work |
| a submit overruns before it was sent (in `caller_id`) | 2 | it was not sent; calling again may work |
| a submit overruns, and `find_by_key` finds it | 0 | submitted |
| a submit overruns, and `find_by_key` finds nothing | 2 | not there; sending the same spec again is safe |
| a submit overruns, and `find_by_key` cannot answer | 1 | unknown; call `list` before submitting again |
| arguments outside the schema | 2 | which field is wrong; the backend is never called |
| `fetch` to an absolute path or through `..` | 2 | the path must stay inside the workspace |

Nothing announces when a job ends: the model is told to have the user ask
again later.
