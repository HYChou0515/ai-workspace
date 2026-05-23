# RCA 3.0 — Backend Plan

Living document for backend work. Frontend has its own plan at
[`plan-frontend.md`](./plan-frontend.md) and a different agent owns it.

**This plan supersedes the generic workspace-app backend plan.** The
project pivoted (per `design_handoff_rca_3.0/`) to a vertical
**Root-Cause Analysis** app for SMT / AOI / yield engineers. The
prior platform work (Sandbox, FileStore, AgentRunner, SSE…) all
remains; what changes is the domain layer on top.

When BE changes the wire (SSE event schema, HTTP routes, contract),
update **§5 Cross-cutting contracts** in the same commit so the FE
agent knows what changed.

---

## 1. Platform foundation — already shipped, RCA reuses 100%

These commits are kept verbatim under the RCA pivot. Nothing here
needs to be re-done; references in the new sections cite them.

| # | Commit    | Scope (will-still-ship-as-is for RCA)                    |
|---|-----------|----------------------------------------------------------|
| 1 | `05e2186` | Sandbox Protocol + MockSandbox                            |
| 2 | `9ae9921` | FileStore Protocol + SpecstarFileStore                    |
| 3 | `74a3960` | Workspace / AgentConfig / Conversation Structs — Workspace is renamed Investigation in §3 |
| 4 | `086e2e1` | Agent tool wrappers (exec/read/write/ls/exists/delete) — RCA adds domain tools on top |
| 5 | `d06e3fb` | FastAPI app factory + SSE message endpoint                |
| 6 | `64518d9` | LocalProcessSandbox + DockerSandbox                       |
| 7 | `8c8520e` | LitellmAgentRunner — model/prompts will be RCA-tuned     |
| 8 | `b4f6db0` | React SPA shell — will be **rebuilt** per RCA design     |
| + | `261ae64` | LitellmAgentRunner retry-with-feedback                    |
| + | `4737d68` | WorkspaceRegistry — renamed InvestigationRegistry         |
| + | `796fa86` | FS ↔ Sandbox sync + Files API                            |
| + | `48a0fa8` | Interrupt + RunCancelled                                 |
| + | `101afe0` | Idle-kill lifecycle (RCA bumps default to 8h, §3.6)      |
| + | `b8cab9e` | Refined SSE event variants (ToolCallParseError, MaxTurnsExceeded) |

161 tests / 100% coverage / ruff & ty clean at the time of pivot.

---

## 2. Pivot scope at a glance

**Core insight**: the backend is a **generic file store + agent +
sandbox**. Everything RCA-specific (5-why structure, fishbone JSON
schema, 8D report shape, hypothesis cells…) is **data the agent
generates and writes to FileStore as files**. The system doesn't model
any of it. It only stores and serves `.md` / `.ipynb` / `.csv` /
`.json` / `.canvas` files; the frontend renders by extension and the
agent produces by following its system prompt.

What changes at the BE level to deliver `design_handoff_rca_3.0`:

- **Schema rename**: `Workspace` → `Investigation`, with new RCA fields
  (severity, line, product, status, owner, description, topics — see §3).
- **Template seeding**: creating an investigation copies a starter set
  of files (sample fixture CSV + skeleton notebooks + `brief.md`). The
  agent fills the views in as the investigation progresses; the
  design's "6-tab snapshot" is the mid-investigation state, not the
  initial one.
- **Notebook execution stack**: VSCode-style ipynb viewer drives a
  whole new sub-stack — `Sandbox.expose_port` Protocol extension,
  `KernelService`, per-cell SSE, cell event types, `PUT /files/{path}`,
  new default sandbox image with `ipykernel + numpy/pandas/matplotlib/scipy`.
- **RCA-tuned `AgentConfig`**: system prompt + tool allow-list encode
  the 8D / 6M / SPC / Pareto workflow knowledge. Tools stay generic
  (`exec`, `read_file`, `write_file`, `ls`, `exists`, `delete_file`).
- **Idle threshold bump**: investigations stay warm 8 hours by default
  (was 15 min for workspace-app).

What we **don't** do in v1:
- Real data integrations (MES / SPC / AOI APIs) — sample CSV fixtures
  ship inside the template.
- **No** `ReportVersion` / `FiveWhyChain` / `Fishbone` / `Hypothesis` /
  `CorrectiveAction` backend resources. Reports become a file-naming
  convention (`/report.v1.md`, `/report.v2.md`, …); the FE picks the
  highest N as current. 5-Why / Fishbone / etc. are agent-written
  `.json` / `.canvas` files with conventions the FE renderer
  understands.
- **No** RCA-specific agent tools (`spc_read`, `defects_aoi`, etc.).
  Agent reads CSV fixtures via `read_file` and writes artifacts via
  `write_file`; the system prompt frames the workflow.
- Multi-tenant or per-org auth — `default-user` continues.
- `run_cell` agent tool — deferred to v1.5.
- `SandboxKilledIdle` event surface — same defer as before.
- Reconnect endpoint (`GET /events?since=`) — same defer.

---

## 3. Schema migration: Workspace → Investigation

Replace `src/workspace_app/resources/workspace.py` with
`investigation.py` and update `register_all`. specstar is in-memory
during dev; no migration concern. Final model after grill alignment
with the (updated) design — `line` removed in favor of `topics`,
`lot` dropped (still appears in agent narration / notebook code but
not as a model field):

```python
from enum import StrEnum
from typing import Annotated

from msgspec import Struct, field
from specstar import OnDelete, Ref


class Severity(StrEnum):
    P0 = "P0"   # halt
    P1 = "P1"   # critical
    P2 = "P2"   # major
    P3 = "P3"   # minor
    P4 = "P4"   # cosmetic


class Status(StrEnum):
    """Per grill-me Q10 + design.
       create → TRIAGING → AWAITING_REVIEW → RESOLVED  (happy path)
                                          └→ ABANDONED  (closed without RC)
    Design's 'draft' state is dropped — we don't create in draft."""
    TRIAGING = "triaging"
    AWAITING_REVIEW = "awaiting_review"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class Investigation(Struct):
    title: str                                            # required
    owner: str                                            # required — user id (resolved via company API)
    description: str = ""                                 # multi-line; replaces design's "initial brief"
    severity: Severity = Severity.P2
    status: Status = Status.TRIAGING
    product: str = ""                                     # part / board, e.g. "MX-7 board"
    members: list[str] = field(default_factory=list)      # user ids
    topics: list[str] = field(default_factory=list)       # free-form tags ("Reflow zone-3", ...)
    attached_agent_config_id: Annotated[
        str | None, Ref("agent_config", on_delete=OnDelete.set_null)
    ] = None
```

```python
# Message: + author (multi-user id) + reasoning (LLM thinking separate from answer)
class Message(Struct):
    role: str                                              # user / assistant / tool / system
    content: str
    author: str | None = None                              # user id when role=user; agent name when role=assistant
    reasoning: str | None = None                           # Qwen3 thinking, o-series reasoning, etc.
    tool_call_id: str | None = None                        # role=tool
    tool_name: str | None = None                           # role=tool


class Conversation(Struct):
    investigation_id: Annotated[
        str, Ref("investigation", on_delete=OnDelete.cascade)
    ]
    messages: list[Message] = field(default_factory=list)
```

```python
# AgentConfig — only the defaults bump
class AgentConfig(Struct):
    name: str
    model: str = "ollama_chat/qwen3:14b"
    system_prompt: str = ""                                # §8 loads RCA prompt here
    allowed_tools: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    sandbox_image: str = "workspace-app/sandbox:py312-ds"  # ← was python:3.12-slim
    idle_timeout_seconds: int = 28800                      # ← was 900 (15min); now 8h per Q10
```

### Not stored (derived elsewhere)

| Design surface | Where it comes from |
|---|---|
| `INC-2026-0142` (id) | specstar's `resource_id`; FE formats the display string |
| `summary` (table row second line) | first line of `description`, FE-derived |
| `sevTone` / `statusTone` | FE color mapping constants |
| `lot` | dropped from model — still appears in agent narration / notebook code as plain text |
| `updated` ("12 min ago") | specstar's `updated_time` + FE relative-format |
| `agent: "running" \| "idle"` | `InvestigationRegistry`'s `session.current_turn` aliveness |
| `pinned` | client-side `localStorage` preference |
| `reportV` / `reportProgress` | derive from `/report.v*.md` file listing + agent run state |

### Why `line` was removed

The latest design dropped `line` (production line) entirely and uses
`topics: list[str]` for the same role plus more — investigations now
tag with concepts like `"Reflow zone-3"`, `"Cell test fixture"`,
`"Contact resistance"`. Home sidebar's TOPICS section groups by these
tags. Table column renamed `Line · product` → `Topic · product`.

**Files:**
- `src/workspace_app/resources/investigation.py` — replaces `workspace.py`
- `src/workspace_app/resources/__init__.py` — `register_all` updates
- All references to `Workspace` (`api/app.py`, `tests/`, etc.) — global
  rename
- `api/registry.py` — `WorkspaceRegistry` → `InvestigationRegistry`,
  `WorkspaceSession` → `InvestigationSession`
- HTTP routes — `/workspaces/{id}/…` → `/investigations/{id}/…`;
  specstar auto-route `/workspace` → `/investigation`

**Tests:** all existing tests work after a rename; add coverage for the
new `Severity` / `Status` enums and the topic-list / members-list
default-factory semantics.

---

## 4. Report versions are a file-naming convention, not a resource

The design has `v1 superseded / v2 superseded / v3 current` semantics
for the 8D report. We **don't** model this as a backend resource — the
agent writes `/report.v1.md`, `/report.v2.md`, …; the FE iterates
`/report.v*.md`, picks the highest N as **current**, renders earlier
versions as **superseded**.

Why not a `ReportVersion` resource:
- Versioning logic is trivially expressible in file names; modeling it
  server-side adds endpoints, schemas, and tests for no real win.
- Keeps the BE blind to "what is a report" — same posture as 5-Why /
  fishbone / hypotheses (all just files).
- Generating a new version = agent calls `write_file("/report.v{N+1}.md", …)`
  using its existing tool. No new BE plumbing.

Whatever metadata the design shows ("summary of what changed in vN",
author, timestamp) the agent can encode in the file's frontmatter or
in a sibling `/report.v3.meta.json` — FE renderer's call.

---

## 5. Template seeding on investigation create

Per Q11-final: design's 6-tab snapshot is the mid-investigation state.
Initial template is the **half-developed scaffold** the agent fills in
over the investigation lifetime.

**v1 template** (`src/workspace_app/rca/templates/default/`):

| Path | Content |
|---|---|
| `brief.md` | Investigation Brief skeleton with `{title}` / `{severity}` / `{line}` / `{product}` / `{description}` substituted at create time |
| `drift.ipynb` | One markdown cell ("# SPC drift analysis"), one empty code cell |
| `pareto.ipynb` | One markdown cell ("# Pareto"), one empty code cell |
| `fishbone.canvas` | Empty 6M JSON skeleton (`{effect: "", branches: [...]}`) |
| `5-why.md` | "## Why #1 …" through "## Why #5 …" placeholder text |
| `report.md` | Initial D1–D8 skeleton headings (this is `report.v0.md`-equivalent — empty draft) |
| `data/.gitkeep` | Empty marker; the `data/` folder is where fixtures land |

Plus a small **fixture CSV** at `data/reflow.zone3.sample.csv` (the
zone-3 timeseries the design's SPC chart is based on). This is the
"sample data" that replaces real MES — the agent can read it via
`read_file` and plot from it inside the notebook.

**Loader:**
```python
# src/workspace_app/rca/templates/__init__.py
def seed_investigation(filestore: FileStore, inv_id: str, inv: Investigation) -> None:
    """Copy the default template into the FileStore, substituting fields."""
    ...
```

Called from the `POST /investigation` route immediately after specstar
creates the resource. specstar's auto-POST handler doesn't know about
this; we add a custom route that wraps it (or use specstar's
`create_action` hook if it has one).

**Files:**
- `src/workspace_app/rca/templates/default/{brief.md, drift.ipynb,
  pareto.ipynb, fishbone.canvas, 5-why.md, report.md,
  data/reflow.zone3.sample.csv}`
- `src/workspace_app/rca/templates/__init__.py` — `seed_investigation()`
- `src/workspace_app/api/app.py` — wire seed call into create path
- Tests cover field substitution + that all template files land in
  FileStore at the right paths

---

## 6. Investigation lifecycle — extended idle + manual close

Per Q10: default `idle_timeout` becomes **8 hours** (was 15 min).
Manual close button = new endpoint that sets `Status.RESOLVED` or
`Status.ABANDONED` and tears down the sandbox.

**Changes:**
- `create_app(idle_timeout=timedelta(hours=8), …)` — flip the default
- New: `POST /investigations/{id}/close` body `{status:
  "resolved"|"abandoned"}` — updates status, releases sandbox via
  `registry.close_session(id)`
- `InvestigationRegistry` grows a `close_session(id)` method (kill
  handle + reverse-sync + remove from registry)

**Files:** `api/registry.py`, `api/app.py`, tests for close endpoint.

---

## 7. Notebook execution stack (from the prior ipynb grill-me)

Notebook execution is the largest single chunk of work. Sub-sections
below were sealed in Q1–Q8 of the ipynb grill-me; recap with RCA
context.

### 7.1 `Sandbox.expose_port`

Add a Protocol method:

```python
class Sandbox(Protocol):
    ...
    async def expose_port(
        self, handle: SandboxHandle, container_port: int
    ) -> tuple[str, int]:
        """Make a port inside the sandbox reachable from the backend.
        Returns (host, host_port) the backend can connect to."""
```

Implementations:
- **MockSandbox**: track calls in a dict; return `("127.0.0.1", container_port)`.
- **LocalProcessSandbox**: noop (sandbox is host); return `("127.0.0.1", container_port)`.
- **DockerSandbox**: re-create the container with `-p 0:container_port` if
  not already, return `(host_ip, mapped_port)`. *Tricky*: Docker
  doesn't let you publish a new port on a running container; we either
  publish a range up-front at create time, or implement port exposure
  via `docker start --publish` semantics which means tear-down/restart.
  v1 candidate: at create time, pre-publish a range (e.g., the 5 ZMQ
  ports for a single kernel; if the user wants multiple kernels we
  extend).

**Files:** `sandbox/protocol.py`, `sandbox/mock.py`, `sandbox/local_process.py`, `sandbox/docker.py`; tests for each.

### 7.2 `KernelService` — per-notebook kernel manager

New module `src/workspace_app/kernels/`:

```python
class KernelHandle:
    notebook_path: str
    client: AsyncKernelClient   # from jupyter_client
    process_handle: ...          # how we spawned ipykernel inside sandbox
    last_cell_run: datetime
    connection_info: dict        # ports + key

class KernelService:
    async def get_or_start(self, session: InvestigationSession,
                           notebook_path: str) -> KernelHandle: ...
    async def interrupt(self, h: KernelHandle) -> None: ...
    async def restart(self, h: KernelHandle) -> KernelHandle: ...
    async def execute_cell(self, h: KernelHandle, code: str
                           ) -> AsyncIterator[CellEvent]: ...
    async def shutdown(self, h: KernelHandle) -> None: ...
```

`InvestigationSession` gains `kernels: dict[notebook_path, KernelHandle]`.
Idle-kill walks each session's kernels and reaps individually idle
ones (per-kernel 30 min after last cell run, while the investigation
itself follows the 8h timer).

**Spawning a long-lived kernel** in the sandbox is an open
implementation question — the current `Sandbox.exec` is fire-and-wait.
v1 candidate: a small `kernel_host.py` shipped in the sandbox image
that the backend invokes via `sandbox.exec(...)`. It double-forks, the
parent prints the connection-info path to stdout and exits, the child
runs `ipykernel_launcher`. Backend `sandbox.download(connection_info)`
to read the ports.

**Files:** `src/workspace_app/kernels/{service.py,host.py}`, tests.

### 7.3 Cell execute SSE endpoint

```
POST /investigations/{id}/notebooks/{path}/cells/{idx}/execute
  body: { code: str }
  → text/event-stream
```

Event types (in `api/events.py`, kept **separate** from `AgentEvent`):

```python
@dataclass(frozen=True)
class CellStream:
    stream: Literal["stdout", "stderr"]
    text: str
    type: Literal["cell_stream"] = "cell_stream"

@dataclass(frozen=True)
class CellDisplayData:
    data: dict[str, str]   # {"text/plain": ..., "image/png": "<base64>", "text/html": ...}
    type: Literal["cell_display_data"] = "cell_display_data"

@dataclass(frozen=True)
class CellError:
    ename: str
    evalue: str
    traceback: list[str]
    type: Literal["cell_error"] = "cell_error"

@dataclass(frozen=True)
class CellDone:
    execution_count: int
    type: Literal["cell_done"] = "cell_done"

CellEvent = CellStream | CellDisplayData | CellError | CellDone
```

Plus restart endpoint:
```
POST /investigations/{id}/notebooks/{path}/kernel/restart  → 204
DELETE /investigations/{id}/notebooks/{path}/cells/{idx}/execute → 204 (interrupt)
```

### 7.4 `PUT /workspaces/{id}/files/{path:path}`

Promoted from "optional" in the prior plan to **required**. FE
auto-saves notebooks after `cell_done` by PUT'ing the updated JSON.

```python
@app.put("/investigations/{id}/files/{path:path}")
async def write_file(id: str, path: str, request: Request):
    body = await request.body()
    await filestore.write(id, "/" + path.lstrip("/"), body)
    return Response(status_code=204)
```

### 7.5 New default sandbox image

Add `docker/Dockerfile.workspace`:

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir \
    ipykernel jupyter_client \
    numpy pandas matplotlib scipy
WORKDIR /workspace
```

Build target: `workspace-app/sandbox:py312-ds`. Update
`AgentConfig.sandbox_image` default. Add this build to
`docker-compose.yml` as a sibling service (one-off build, not a
running container).

Backend `pyproject.toml` gains `jupyter_client` as a runtime
dependency (for talking to the kernel from outside the sandbox).
`ipykernel` also goes in the backend env so LocalProcessSandbox-based
testing doesn't need a separate setup.

---

## 8. Agent surface — generic tools + RCA system prompt

**No new tools.** Existing generic tools cover everything:
`exec`, `read_file`, `write_file`, `ls`, `exists`, `delete_file`.

The RCA-specific behaviour is all in **AgentConfig.system_prompt**.
The prompt teaches the agent:
- The 8D / 6M / SPC / Pareto workflow vocabulary.
- The conventional file paths and shapes the FE renders
  (`/data/*.csv` is fixture data, `5-why.md` is structured under
  `## Why #N` headings, `fishbone.canvas` is the 6M JSON schema,
  reports are written as `report.vN.md` with the highest N being
  current, etc.).
- How to use the fixture CSVs in `/data/` as if they were real SPC /
  AOI data — the seeded template ships these.

The system prompt + sample fixture data + template skeletons together
produce the RCA UX *without* any system-side domain modeling.

**Files:**
- `src/workspace_app/rca/prompts/system.md` — RCA agent system prompt
  (multi-page document; ships as a string constant or text file the
  default `AgentConfig` loads at startup)
- `src/workspace_app/rca/templates/default/data/*.csv` — fixture data
  (lives in the template, copied per-investigation)
- Tests cover that the prompt loads and an investigation's default
  `AgentConfig` references it

**Why not even keep the 3 confirmed "real" tools** (`spc.read`,
`defects.aoi`, `correlate.find`) shown in the design's agent log?
Because they'd be thin shims over `read_file` + Python execution
inside the notebook. Skipping them keeps the tool surface tight; the
agent's chat narration can still say "I'm calling spc.read on
reflow.zone3" — the FE doesn't care whether that's a literal tool
invocation or freeform narration.

---

## 9. API routes consumed by SPA

| Route | Purpose | Status |
|---|---|---|
| `GET /investigation` (specstar auto) | list investigations | needs schema rename ⏳ |
| `POST /investigation` (custom-wrapped) | create + seed template + bump status to triaging | ⏳ §3, §5 |
| `GET /investigation/{id}` | get one | needs rename ⏳ |
| `PATCH /investigation/{id}` (specstar auto) | update fields | needs rename ⏳ |
| `POST /investigations/{id}/messages` | start an agent turn → SSE | needs rename ⏳ |
| `DELETE /investigations/{id}/messages/current` | interrupt | needs rename ⏳ |
| `POST /investigations/{id}/close` | manual close, set status, tear sandbox | ⏳ §6 |
| `GET /investigations/{id}/files[?prefix=]` | list files | needs rename ⏳ |
| `GET /investigations/{id}/files/{path:path}` | read file | needs rename ⏳ |
| `PUT /investigations/{id}/files/{path:path}` | write file (FE save notebooks here) | ⏳ §7.4 |
| `POST /investigations/{id}/notebooks/{path}/cells/{idx}/execute` | run cell → SSE | ⏳ §7.3 |
| `DELETE /investigations/{id}/notebooks/{path}/cells/{idx}/execute` | interrupt cell | ⏳ §7.3 |
| `POST /investigations/{id}/notebooks/{path}/kernel/restart` | restart kernel | ⏳ §7.3 |

Reports use the file-naming convention `/report.v{N}.md` (§4); FE
iterates these via `GET /investigations/{id}/files?prefix=/report.v`
and picks the highest N as current. No dedicated `/reports` endpoints.

---

## 10. Cross-cutting contracts (FE/BE sync surface)

Anytime BE changes one of these, the FE agent must mirror in
`web/src/events.ts` (events) or update `fetch` paths (routes). Update
this section in the same commit that changes the wire.

### SSE events

Two **separate** unions — `AgentEvent` (existing) and `CellEvent` (new).

`AgentEvent` from prior pivot:
- `MessageDelta`, `ToolStart`, `ToolEnd`, `RunDone`, `RunError`
- `RunCancelled`, `ToolCallParseError`, `MaxTurnsExceeded`
- `SandboxKilledIdle` — still deferred

`CellEvent` for notebook execution (§7.3):
- `CellStream { stream, text }`
- `CellDisplayData { data: mime → string }`
- `CellError { ename, evalue, traceback }`
- `CellDone { execution_count }` (terminal)

### Brand assets

FE will import SVGs from `design_handoff_rca_3.0/assets/`. Backend
serves them as static if FE needs them via the same host:
- `rca-mark.svg`, `rca-mark-light.svg`, `rca-logo-horizontal.svg`, `favicon.ico`

Probably cleanest: copy to `web/public/` at FE build time.

---

## 11. Principles

- **Protocol-first.** Adding a method to `Sandbox`/`FileStore`/
  `KernelService` means updating all impls (Mock + Local + Docker)
  and writing tests for each.
- **Bias to in-process state for v1.** Registry, dirty-path trackers,
  kernel handles — all in-memory.
- **The system is RCA-agnostic.** All RCA structure (8D, 6M, 5-Why,
  hypothesis cells, correctional actions) lives in the agent's
  system prompt + the files it writes, never in BE resources.
  Fixture data ships inside `rca/templates/default/data/`.
- **Tests first via `/tdd`.** Red→green vertical-slice.
- **Honesty over scope creep.** Split + update this doc rather than
  letting items sprawl.

---

## 12. Order of work

1. **Schema rename** (§3) — Workspace → Investigation, fields,
   registry/route renames. Cleanup before any new work goes in.
2. **Template seeding** (§5) — 6-file skeleton + sample CSV fixtures
   + create-flow wiring.
3. **RCA system prompt** (§8) — load + plug into default `AgentConfig`.
4. **Idle-bump + manual close** (§6) — small, lands before kernel work
   adds to the lifecycle complexity.
5. **`PUT /files/{path}`** (§7.4) — small, unblocks FE notebook save.
6. **Notebook execution stack** (§7.1 → §7.5) — biggest chunk:
   - 7.5 sandbox image first (Dockerfile + build target)
   - 7.1 Sandbox.expose_port (Mock + Local; Docker can come right after)
   - 7.2 KernelService (start/shutdown/execute_cell + tests)
   - 7.3 Cell SSE endpoint + CellEvent types

No ReportVersion or domain-tool steps — those collapsed per §4 / §8.

Lower priority / deferred to v1.5:
- `run_cell` agent tool (Q6).
- `SandboxKilledIdle` (still needs registry refactor).
- Reconnect endpoint (`GET /investigations/{id}/events?since=`).
- AdapterVolumeFileStore (still nobody asked).
- 5-Why structured editor backend support.
- Fishbone `.canvas` schema + editor backend support.
