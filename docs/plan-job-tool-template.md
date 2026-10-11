# Plan — job tool template: we define the interface, an operator writes the backend

## Problem

Someone wants a tool that submits GPU jobs. The next one will want batch reports,
then an approval flow. Each is the same shape — submit something that runs
elsewhere, ask about it later — and each author would otherwise rediscover the
same failure modes alone:

- a submit that timed out after reaching the backend, retried by the model, is
  **two jobs** (two bills, for GPUs);
- a status call that hangs eats the platform's 60 s ceiling and returns nothing
  the model can act on;
- "the backend did not answer" read as "the job does not exist";
- what the model is told after each outcome, written differently per author and
  drifting.

So the platform side defines the interface **once** — shape, per-call time
budget, and what each outcome means — and the operator only writes the part
that talks to their backend.

## Position

- It is a **template**, not a platform feature: it is copied, the author owns
  every line (100 % control), and nothing in `src/workspace_app` knows it exists.
  To ai-workspace it is one more third-party tool.
- It depends on the **published** tool contract only (`docs/tool-authoring.md`:
  three-stage CLI, exit codes, 60 s ceiling) and imports nothing from
  ai-workspace.
- It is not limited to GPUs. Nothing in it names a domain.

## Decisions

Source of each: **user** = chosen by the user in the 2026-10-11/12 grill;
**mine** = settled by me during the grill, listed for the user to overturn.

| # | Decision | Source |
|---|---|---|
| D1 | A standalone **job tool template**, generic over domains | user |
| D2 | **Copied**, the author owns it 100 %; the backend (impl) is a **separate package**, so changing the impl and upgrading the template are independent | user |
| D3 | The shape tests **block the build by default** (CI runs them before `build-tool`); the author may remove that step | user |
| D4 | Tests check **shape only** — no backend, no mocks | user |
| D5 | `update(job_id, patch)`'s content is the author's (`patch_schema`); suspend/priority/… are impl details. `cancel` is optional | user |
| D6 | Values defined **once** live on the impl's `Contract`: `spec_schema`, `idempotency`, `budget`, `patch_schema`. No deployment override of the budget | user |
| D7 | Required `caller_id()`: identity is whoever the backend recognises from the credential | user |
| D8 | What the model reads: the **behaviour** half is generated from the `Contract`; the author writes only the **domain** half | user |
| D9 | Lives in a folder of this repo, `sample-tools/job-tool-template/` (a separate repo was chosen first, then dropped for simplicity); still imports nothing from ai-workspace | user |
| D10 | No completion notification; the generated description tells the model to have the user come back and ask | user |
| D11 | Three packages, one-way dependencies: `contract/` ← `example-impl/` ← `template/` (and `template/` → `contract/`) | user |
| D12 | `phase ∈ {running, done, unknown}` is the only state the template defines; everything else is the author's `state` string + `detail` dict | mine |
| D13 | Required: `submit / find_by_key / get / list / caller_id`. Optional: `update / cancel / logs / fetch`; an optional method not overridden is **not offered** to the model | mine |
| D14 | Exceptions → exit codes: `Retryable`→2, `NeedsAction`→3, `NotFound`→1; timeouts are produced by the shell | mine |
| D15 | Budgets have defaults; `Contract` refuses at load any budget outside `(0, 60 − 5]` | mine |
| D16 | After a submit timeout the shell calls `find_by_key` itself; only when that also fails is the model told "outcome unknown — call list before submitting again" | mine |
| D17 | No "destructive" flag: nothing in ai-workspace reads one (grep of `tooling/`, `agent/`, `tool-builder/` finds the word only in comments, never a flag that is read). A dangerous change is a separate command or a sentence in its description | mine |
| D18 | No job-record file in the workspace: `list` is required and scoped by credential | mine |
| D19 | Env vars are declared on the `Contract`; `env.json` is generated from it and a shape test pins the two equal | mine |

### Settled while building (mine, for the user to overturn)

| # | Decision | Why |
|---|---|---|
| D20 | `budget.submit + budget.find_by_key ≤ 55`, refused at declaration | A submit that runs out of time is followed by `find_by_key` in the same call; two budgets of 55 each would overrun the platform's 60 s before the shell could say anything |
| D21 | `caller_id` runs inside the submit budget; a submit stuck there is "most likely not sent — sending the same spec again is safe", exit 2 | The key is not known yet, so `submit` was not called. Not airtight: `caller_id` may return in the instant before the process ends and the thread call `submit`; the same key makes a resend harmless either way |
| D22 | After a submit timeout, `find_by_key` returning None is exit 2 "sending the same spec again is safe"; failing to answer is exit 1 "call list first" (D16), or 3 when it raised `NeedsAction` | None is the backend saying it has nothing; the same spec carries the same key, so a late arrival is returned, not doubled |
| D23 | Arguments are validated against the schema the model was shown (`jsonschema`, a new template dependency) before the backend is called; failures are exit 2 naming the field | Arguments are held to the schema the backend declared — which admits undeclared fields unless that schema says `additionalProperties: false` (the README tells authors) |
| D24 | `fetch` refuses a `dest` that is empty, absolute, through `..`, or resolves outside the working directory (a symlink), exit 2, before the backend is called | The working directory is the user's workspace |
| D25 | `Contract.env` defaults to None ("did not say"; no `env.json`), distinct from `()` ("needs none"; `[]`) | #750's three states: absent is not empty |
| D26 | A backend implementing `update` must declare `patch_schema`, and the reverse | One without the other is a call the model cannot use correctly, or a schema for a call that does not exist |

### Settled in review round 1 (mine, for the user to overturn)

All four lenses ran on `d8ec74c5`; regression found nothing, the other three
found the items below. Each fix began with a test that was red on the unfixed
code.

| # | Decision | Why |
|---|---|---|
| D27 | Class-level checks (a contract declared, every required call implemented, D26) live in `JobBackend.check()`, applied by `load_backend` — which the CLI and the shape tests both run. Nothing is checked at class definition | They ran only at instantiation, which stages 1–2, the shape tests and the builder smoke never do, so a backend without `get` shipped. Definition time was also wrong: it refused a base class that only shares helpers |
| D28 | Once a call is abandoned, the installed command (`cli.entry`) ends the process with `os._exit` as soon as the answer is out | A backend waiting on a thread pool (boto3, gRPC) kept the process alive: the interpreter joins pool workers at exit. Measured 30.1 s against a 0.2 s budget before, 0.3 s after |
| D29 | stdout is swapped to stderr while the backend runs; the answer goes to the original stdout | A backend's `print` corrupted the answer |
| D30 | Anything the backend raises — `SystemExit` included — is exit 1 "the backend failed: …" | `sys.exit(0)` in a backend was exit 0 with no output: a failure dressed as success |
| D31 | The answer is made JSON-safe before it is written (NaN/inf and non-JSON values become text) | A datetime in `detail` turned a successful cancel into a reported failure |
| D32 | Whole-number floats in validated arguments become `int` | JSON Schema counts `5.0` as an integer, so it gave the same spec a second key, and broke backends that slice with it |
| D33 | `list.limit` ≤ 100; a non-string `next_cursor` is made a string; extra argv is exit 2; `Budget` refuses a non-number (`True`, `"10"`) | Each was a call the model could make that either failed later or could never be followed |
| D34 | The advice after `done` names outputs as a possibility ("fetch saves its outputs, if it produced any") | The shell reads only `phase`, and `done` includes cancelled jobs, which have no outputs |

### Known, not guaranteed

- **Idempotency rests on the author honouring the docstring.** D4 rules out a
  test that submits twice. The shell's `find_by_key` (D16) narrows the window; it
  does not close it.
- **A shared credential is a shared identity** (D7): a token in the item layer
  makes every participant the same caller — they see each other's jobs and an
  identical spec dedupes across people. Documented for authors, not prevented.

## The interface (target shape)

```python
# contract/  — package `job-contract`, depends on nothing
Phase = Literal["running", "done", "unknown"]

@dataclass(frozen=True)
class JobStatus:
    job_id: str
    phase: Phase             # the only field the shell reads
    state: str               # author's
    detail: dict[str, Any]   # author's, printed verbatim

class JobError(Exception): ...     # exit 1
class Retryable(JobError): ...     # exit 2
class NeedsAction(JobError): ...   # exit 3 — name the variable in full
class NotFound(JobError): ...      # exit 1 — certain absence, ≠ phase="unknown"

@dataclass(frozen=True)
class Budget:                      # seconds; enforced by the shell at run time
    submit: float = 10; find_by_key: float = 5; get: float = 3; list: float = 5
    update: float = 10; cancel: float = 10; logs: float = 5; fetch: float = 50

@dataclass(frozen=True)
class Idempotency:
    fields: tuple[str, ...]        # which spec fields make two submits "the same"
    per_user: bool = True
    def key(self, spec: dict, *, caller: str) -> str: ...   # the ONLY derivation

@dataclass(frozen=True)
class EnvVar:
    name: str; description: str | None = None; required: bool | None = None

@dataclass(frozen=True)
class Contract:
    spec_schema: dict
    idempotency: Idempotency
    budget: Budget = Budget()
    patch_schema: dict | None = None
    domain_description: str = ""   # D8: the author's half
    env: tuple[EnvVar, ...] | None = None   # D25: None = did not say

class JobBackend(ABC):
    contract: ClassVar[Contract]
    @classmethod
    def check(cls) -> None: ...    # D27: run by load_backend
    @abstractmethod
    def caller_id(self) -> str: ...
    @abstractmethod
    def submit(self, spec: dict, key: str) -> str: ...
    @abstractmethod
    def find_by_key(self, key: str) -> JobStatus | None: ...
    @abstractmethod
    def get(self, job_id: str) -> JobStatus: ...
    @abstractmethod
    def list(self, cursor: str | None, limit: int) -> tuple[list[JobStatus], str | None]: ...
    def update(self, job_id: str, patch: dict) -> JobStatus: raise NotImplementedError
    def cancel(self, job_id: str) -> JobStatus: raise NotImplementedError
    def logs(self, job_id: str, offset: int, limit: int) -> tuple[str, int]: raise NotImplementedError
    def fetch(self, job_id: str, name: str, dest: str) -> str: raise NotImplementedError
```

```
template/  — what an author copies; the tool repo itself
  pyproject.toml        one [project.scripts]; depends on job-contract + the impl
  src/job_tool/cli.py   three-stage contract; commands derived from the impl
  src/job_tool/run.py   budget enforcement, exception → exit code, D16
  src/job_tool/describe.py   behaviour text from the Contract (D8)
  env.json              generated from Contract.env (D19)
  tests/test_shape.py   D3/D4
  .gitlab-ci.yml        pytest, then build-tool
example-impl/  — a demo backend, depends on job-contract only
```

The impl is found through one entry point, group `job_tool.backend`, exactly one
registered; zero or two refuse to start, naming what was found.

## Phases

Each phase is `/tdd`, one commit.

- **P1 — `contract/`.** `Budget` bounds, `Idempotency.key` (stable across
  dict order, `per_user` includes the caller), `Contract` refuses
  `idempotency.fields` missing from `spec_schema` and a bad budget at
  construction, `JobBackend.check()` refuses a subclass missing a required
  method (applied when the backend is loaded, D27).
- **P2 — impl discovery + the command list.** Entry-point lookup (zero / one /
  two). Commands offered = required + overridden optional (D13). Stage 1 and
  stage 2 of the CLI contract, schemas from `spec_schema` / `patch_schema`.
- **P3 — running a call.** Budget enforcement (the impl call runs in a worker
  thread; on expiry the shell prints its answer and exits the process, so a
  stuck call cannot outlive its budget). Exceptions → exit codes (D14). `phase`
  → what the model is told. Submit timeout → `find_by_key` (D16).
- **P4 — descriptions.** Behaviour sentences generated from the `Contract`
  (submit returns at once; unknown ≠ absent; update = accepted, not applied;
  no notification, D10), joined with `domain_description`.
- **P5 — `env.json` from the Contract (D19)**, a regenerate command, and the
  shape test that pins them equal.
- **P6 — `example-impl/`.** A demo backend that keeps its jobs under a folder
  in the workspace and reports `done` after a set time. Labelled a demo
  everywhere; it exists so the template runs and its shape tests have a target.
- **P7 — the template as a product.** `tests/test_shape.py`, `.gitlab-ci.yml`
  (pytest before `build-tool`), README and a `CLAUDE.md` brief for authors in
  the same voice as `tool-starter/`'s.
- **P8 — ai-workspace side.** A test in `tests/tooling/` that runs the template
  through the real three-stage contract (the way `test_end_to_end.py` does for
  `tool-starter/`), and a pointer from `docs/tool-authoring.md`.

No `docs/migrations.md` entry: nothing an operator of ai-workspace must do.

## Open risks

- **Path dependencies.** In this repo `template/` reaches `contract/` and
  `example-impl/` by relative path. The GitLab builder mounts only the tool repo,
  so a real author must point both at a git URL or an index; the README says so,
  and P8 cannot prove it (it builds on the host). Checked: the editable path
  sources do NOT leak into the bundle — prebuild installs them from `file://`,
  and the only `.pth` in the built bundle is virtualenv's own. Found in round
  1: they DO go stale — uv caches a wheel by (name, version) and prebuild
  refreshes only the tool's own package, so after editing `contract/` the P8
  build bundled the old one. P8 cleans the two from uv's cache first; the
  README tells authors who build from `../` paths.
- **Killing a stuck call.** A thread cannot be cancelled in Python; P3 ends the
  process instead. That is right for a one-shot CLI and wrong for anything
  long-lived. Both callers today are one-shot: the sandbox runs the launcher
  per call, and so does the MCP runner (`tooling/mcp_server.py:142`,
  `subprocess.run` per call with a 60 s timeout). A future caller that keeps
  the process alive would break this.
