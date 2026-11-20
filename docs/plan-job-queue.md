# Plan — job queue: send a folder to another machine, and get it back

The job tool template (`plan-job-tool-template.md`, #907) defined calls a
backend makes — `submit(spec)`, `get`, `list` — and stopped there. Used for what
it was meant for, it does too little: the work the AI prepared in its sandbox
never reaches the machine that runs it, nothing comes back, and the model cannot
see the queue. This plan replaces its interface. It is still a template, still a
third-party tool, and still imports nothing from ai-workspace.

## Problem

The AI and the work live on different machines. The AI writes a training script
in the item's sandbox and tries it on a small slice there; the real run needs a
GPU cluster, a batch farm, a host with a licence. Today, between the two, a
person carries everything by hand: the script and its data to the cluster, the
outputs back to the workspace, "is it done yet" to and fro. The template's
`submit` takes a JSON spec, so the folder the AI just wrote cannot be sent at
all.

## Decisions

**Source** says who decided: **user** = the user's answer in the design talk
(2026-11-19/20); **mine** = an implementation choice, listed for overturning.

| # | Decision | Source |
|---|---|---|
| D1 | No new platform mechanism: the third-party tool (its MCP face included) does all of it — no schedule, page or skill is stitched in | user |
| D2 | Nothing wakes the AI when a job ends: a person comes back and asks, and that one question collects everything (`jobs`) | user |
| D3 | A job is a **folder of the workspace** (`workdir`) plus the resources it asks for. What it runs, which packages it installs, its environment variables are **files in that folder**; their names and meaning are the backend's rules, not the tool's | user |
| D4 | The backend writes the description of `run` — how to lay out the folder, what the machine has (Python, CUDA, network, datasets, secrets already set). The tool appends only the sentences about its own behaviour. Other commands have default descriptions a backend may override | user |
| D5 | Secrets are the execution side's (cluster account, secret store). The environment panel (`env.json`) carries only what the tool itself needs to reach the backend | user |
| D6 | Large folders are uploaded in pieces over several calls, and the job starts only once everything has arrived. Downloads resume the same way | user |
| D7 | The tool decides the job id and passes it in; starting the same id twice starts one job | user (kept over my proposal to return the backend's id) |
| D8 | Required calls: `upload`, `start`, `status`, `list_files`, `download`, `logs`, `cancel`. Optional: `capacity` — without it there is no `queue` command | user (chose these over a three-call minimum) |
| D9 | `queue` shows workers and the queue; `jobs` shows each job's place in it. Priority is a field of the backend's resource schema; quota is enforced by the execution side and only reported (an error from `start`, or a field of `capacity`) | user |
| D10 | No `sweep` and no `rerun`: the AI calls `run` again | user |
| D11 | Sameness: the same item, the same folder content and the same resources are the same job **until it finishes**; after that the same request starts a new one | mine |
| D12 | The tool keeps a record per job in the workspace, `jobs/<job_id>/job.json` — visible in the file tree, and how `jobs` knows which jobs are this item's. It never sends `jobs/` | mine |
| D13 | Packing skips `.git/`, virtualenvs, `node_modules/`, caches, `jobs/`, symlinks, and what the folder's `.gitignore` names | mine |
| D14 | Upload progress is read from the backend (`list_files` sizes), never from the tool's own memory: a call killed halfway cannot make the tool believe a file arrived. An upload never truncates, so a late duplicate write of the same bytes is harmless | mine |
| D15 | Phases: `queued`, `running`, `succeeded`, `failed`, `unknown`. A cancelled job is `failed` with its state saying so | mine |

### Not doing

- Polling, notifications, a jobs page (D1, D2).
- A job-specific environment variable mechanism (D3, D5).
- The previous template's per-field sameness, `identity()` override, 24 h window
  and `find_by_key` (D7, D11 replace them).

## The interface

```python
class MyBackend(JobBackend):
    run_description = "..."          # D4, required
    resources_schema = {...}         # JSON schema of what a job may ask for
    descriptions = {"logs": "..."}   # optional overrides
    env = (EnvVar("CLUSTER_TOKEN", required=True),)   # optional: the tool's own variables
    budget = Budget(upload=20)       # optional: seconds per call

    def upload(self, job_id, path, offset, data: bytes) -> None
    def start(self, job_id, resources) -> None
    def status(self, job_id) -> Status            # NotFound when there is no such job
    def list_files(self, job_id, path) -> list[Entry]   # recursive; works before start
    def download(self, job_id, path, offset, limit) -> bytes
    def logs(self, job_id, offset, limit) -> tuple[str, int]
    def cancel(self, job_id) -> None
    def capacity(self) -> Capacity               # optional
```

Errors: `Retryable` (exit 2), `NeedsAction` (exit 3, names the variable),
`NotFound`, anything else (exit 1).

## Commands

| Command | What it does |
|---|---|
| `run(workdir, resources)` | Pack, hash, reuse or create the job, upload what is missing until time runs short, then `start`. Answers "uploaded X of Y, call run again" or "started" |
| `jobs()` | This workspace's records, each with a fresh `status`; uploads still owed are listed as such |
| `queue()` | `capacity()`, when the backend has it |
| `files(job_id, path)` | `list_files` |
| `fetch(job_id, path, dest)` | Download into the workspace (default `jobs/<job_id>/fetched/<path>`), resuming from a `.part` file |
| `logs(job_id, offset?)` | A page of the log; the end of it when no offset is given |
| `cancel(job_id)` | `cancel`, then its status |

## Phases

| Phase | Content | Done when |
|---|---|---|
| P1 | `job_contract`: types, `JobBackend`, `check()` | contract tests green |
| P2 | Packing: walk, skip rules, file hashes, job hash | pack tests green |
| P3 | `run`: records, upload loop, resume, start | run tests green |
| P4 | `jobs`, `queue`, `files`, `fetch`, `logs`, `cancel`, descriptions, CLI, shape tests | template tests green |
| P5 | Demo backend (a local folder as the machine) | demo tests green |
| P6 | Docs (`template/README.md`, `CLAUDE.md`, `tool-authoring.md`), the integration test | `tests/docs`, `mkdocs --strict`, the integration test |

No `docs/migrations.md` entry: nothing an operator of ai-workspace must do; the
template ships in no image.
