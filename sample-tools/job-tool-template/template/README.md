# Job tool template

A tool for work the AI prepares in its sandbox and another machine runs: GPU
training, a batch report, a simulation on a licensed host. The AI writes a
folder; the tool sends it to your machines, starts it, and brings back what
it wrote. You write the part that moves bytes to your machines and starts
things there; this template is everything else.

## Three packages

```
contract/       job-contract — the interface (JobBackend) and the values it passes
template/       THIS — the tool itself. Copy it as your tool repository.
example-impl/   example-jobs — a DEMO backend. Copy it as the start of yours.
```

Dependencies run one way: your backend depends on `job-contract`; this tool
depends on `job-contract` and your backend. Changing the backend is a new
version of the backend package; upgrading the template never touches it.

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

## What the model can do

| Command | What it does |
|---|---|
| `run(workdir, resources)` | Send a folder of the workspace and start it. A large folder takes several calls; each answers how much has arrived, and the job starts once all of it has |
| `jobs()` | The jobs this workspace sent, newest first (20 unless asked for more), each with where it is now — the one call "is it done yet?" needs |
| `queue()` | The machines, the jobs waiting, the quota left. Only when your backend has `capacity` |
| `files(job_id, path)` | What a job has in its folder over there |
| `fetch(job_id, path, dest)` | Copy a file or folder back into the workspace (`jobs/<job_id>/fetched/<path>` by default); a large one over several calls |
| `logs(job_id, offset)` | The job's log; the end of it unless an offset is given |
| `cancel(job_id)` | Stop it |

Nothing announces when a job ends: the model tells the person to ask later,
and that one question (`jobs`) collects everything.

## What you write

```python
class MyBackend(JobBackend):
    run_description = "..."     # how to lay out a folder for you — the model reads only this
    resources_schema = {...}    # JSON schema of what a job may ask for

    def upload(self, job_id, path, offset, data): ...
    def start(self, job_id, resources): ...
    def status(self, job_id) -> Status: ...
    def list_files(self, job_id, path) -> list[Entry]: ...
    def download(self, job_id, path, offset, limit) -> bytes: ...
    def logs(self, job_id, offset, limit) -> tuple[str, int]: ...
    def cancel(self, job_id): ...

    def capacity(self) -> Capacity: ...   # optional: gives the model `queue`
```

The docstrings in `contract/src/job_contract/backend.py` say what each must
do. The ones that matter most:

- **`run_description` is the whole manual.** The model writes the folder from
  it alone: which file you run and how (`bash run.sh`), which files set up the
  environment (`requirements.txt` installed first, if present; otherwise your
  default image), where variables go (`.env`), what the machine has (Python,
  CUDA, datasets at `/data`, no network), and which secrets are already set
  there. File names and their meaning are yours; the only file the tool
  reads is the folder's top-level `.gitignore`, to leave out what it names.
- **`upload` writes at `offset`, never truncates.** The tool sends a folder in
  pieces over as many calls as it takes, and reads what arrived from
  `list_files` sizes — so a piece sent twice must leave the same file, and
  `list_files` must work before `start`.
- **`start` of an id already started does nothing.** Its answer can be lost;
  the tool may call again. It is called only once every file has arrived.
  Refusing for quota: `Retryable` when waiting will free it, `NeedsAction`
  when a person must act.
- **`status` raises `NotFound` for a job never started; `phase="unknown"`** when
  you cannot ask just now. `phase` is one of `queued`, `running`,
  `succeeded`, `failed`, `unknown`; a cancelled job is `failed`. Your own word
  for it goes in `state`; queue place, ETA and machine in `position`, `eta`,
  `worker`.
- **`env` is what THIS TOOL needs to reach you** — a token, an endpoint. It
  becomes `env.json` and the person's environment panel. A job's own variables
  are files in its folder; its secrets live on your side.

Priority is a field of `resources_schema` if your machines have one. Quota is
yours to enforce; `capacity().quota` shows it.

Raise `Retryable` when calling again may work, `NeedsAction` when a person
must act first — naming the variable in full (`set CLUSTER_TOKEN`), which is
what lets the platform offer them a way to set it — and anything else for a
plain failure.

## What the tool does so you do not have to

- **Which files are a job.** The folder, without folders named `.git`,
  `.venv`, `venv`, `node_modules`, `__pycache__` and a few other caches,
  without `.pyc` files and links, and without what its top-level `.gitignore`
  names. The workspace's own `jobs/` records folder is never sent.
- **Which job it is.** The item, the resources and every file's content make
  the job; the same request while it is unfinished is that job, never a
  second one. After it finishes, the same request starts a new one — and if
  the records were deleted, ids you already have are skipped. Job ids
  are lowercase letters, digits and `-`, short enough for a k8s name.
- **Large folders.** Uploads and downloads are split into pieces, each within
  its budget, over as many calls as it takes. An upload resumes from the
  sizes `list_files` reports, and starts only on your word that every file is
  whole; a download resumes from its own partial file in the workspace.
- **Time.** Each call has a budget (`budget = Budget(...)`; defaults: upload
  20, start 15, status 5, list_files 10, download 20, logs 5, cancel 10,
  capacity 10 seconds); one that overruns is abandoned and the model is told
  what that means. Calls one command makes back to back must fit the
  platform's ceiling together, and `Budget` refuses values that cannot. A
  slow `start` is checked with `status` before anything is said.
- **stdout is only the answer.** Anything your code, a library or a command
  you run (`sbatch`, `kubectl`) writes to stdout goes to stderr instead.
- **Records.** Each job gets `jobs/<job_id>/job.json` in the workspace: what
  was sent, with what resources, and the last status seen.
