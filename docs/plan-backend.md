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

What needs to change at the BE level to deliver `design_handoff_rca_3.0`:

- **Schema rename**: `Workspace` → `Investigation`, with new RCA fields
  (severity, line, product, topics, status, owner, description).
- **New domain resource**: `ReportVersion` for versioned 8D reports
  (`v1 · superseded`, `v2 · superseded`, `v3 · current` semantics).
- **Template seeding**: creating an investigation copies a starter set
  of files (just `brief.md` for v1 — design's 6-tab snapshot is the
  *mid-investigation* state, not the initial one).
- **Notebook execution stack**: VSCode-style ipynb viewer drives a
  whole new sub-stack — `Sandbox.expose_port` Protocol extension,
  `KernelService`, per-cell SSE, cell event types, `PUT /files/{path}`,
  new default sandbox image with `ipykernel + numpy/pandas/matplotlib/scipy`.
- **Domain agent tools** (mock-only for v1): `spc_read`, `defects_aoi`,
  `correlate_find`, `pareto_build`, `fishbone_draft`, `fivewhy_draft`,
  `report_generate` — alongside the existing generic tools.
- **Idle threshold bump**: investigations stay warm 8 hours by default
  (was 15 min for workspace-app).

What we **don't** do in v1:
- Real data integrations (MES / SPC / AOI APIs) — all mock.
- Multi-tenant or per-org auth — `default-user` continues.
- Fishbone `.canvas` editor — read-only display only.
- 5-Why structured editor — `.md` for v1, structured later.
- `run_cell` agent tool — deferred to v1.5.
- `SandboxKilledIdle` event surface — same defer as before.
- §3.7 Reconnect endpoint — same defer.

---

## 3. Schema migration: Workspace → Investigation

Replace `src/workspace_app/resources/workspace.py` with
`investigation.py` and update `register_all`. We're not preserving
backwards compat with any old data because specstar is in-memory
during dev; field-rename is the migration.

```python
from enum import StrEnum
from msgspec import Struct, field

class Severity(StrEnum):
    P0 = "P0"   # critical
    P1 = "P1"   # high
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"   # informational

class Status(StrEnum):
    TRIAGING = "triaging"          # default state on create
    AWAITING_REVIEW = "awaiting_review"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"
    # draft + closed-manual handled by status transitions, not stored separately

class Investigation(Struct):
    title: str                                    # required
    description: str = ""                         # multi-line, replaces design's "Initial brief"
    topics: list[str] = field(default_factory=list)  # tag list — left-sidebar TOPICS section groups by these
    severity: Severity = Severity.P2
    line: str = ""                                # production line ("SMT 1")
    product: str = ""                             # product code
    owner: str = "default-user"                   # auto-set; future SSO will replace
    status: Status = Status.TRIAGING
    members: list[str] = field(default_factory=list)
    attached_agent_config_id: str | None = None
    # lot / sparkline / agent-running flag — derived, not stored
```

**Removed** (vs design): `lot`, `draft`, separate `pinned` flag (use a
client-side preference or specstar metadata).

`AgentConfig` and `Conversation` stay as-is; the RCA-specific
prompt + tool subset lives in `AgentConfig.system_prompt` +
`AgentConfig.allowed_tools`.

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
new `Severity`/`Status` enums and topic-list semantics.

---

## 4. New domain resource: `ReportVersion`

The design's 8D report has versioned semantics (exactly one `current`
per investigation, prior versions become `superseded`, optional
`Generate new version` action). specstar's auto version-history is
*per-resource-update*, not the "promote-to-current" UX we want, so
ReportVersion is a separate resource that tracks the supersession
manually.

```python
class ReportVersion(Struct):
    investigation_id: str
    version_number: int            # 1, 2, 3...
    is_current: bool               # exactly one True per investigation_id
    summary: str                   # "What changed in v3"
    body_path: str                 # e.g. "/report.v3.md" — actual content in FileStore
    generated_by: str              # "agent + Alice"
    generated_at: datetime
```

**Why path-to-FileStore instead of inline content:**
keeping report markdown in FileStore lets the editor render it with
the same `.md` viewer as `brief.md`, and the agent's `write_file`
tool keeps working unchanged. The `ReportVersion` resource is just
the version metadata + pointer.

**Endpoints:**
- `GET /investigations/{id}/reports` → list versions (sorted desc)
- `POST /investigations/{id}/reports/generate` → create new v(N+1)
  - body: `{ summary: str, body: str }` (body is the markdown that
    becomes `/report.v{N+1}.md` in FileStore)
  - server flips the previous `is_current` to False, creates the new
    one with `is_current=True`
- `GET /investigations/{id}/reports/{v}` → metadata + body bytes via
  the body_path
- (No DELETE for v1 — supersession is the deletion model.)

**Files:**
- `src/workspace_app/resources/report_version.py`
- `src/workspace_app/api/reports.py` (new sub-router) — or fold into app.py
- Tests under `tests/api/test_reports.py`

---

## 5. Template seeding on investigation create

Per Q11-final: design's 6-tab snapshot is mid-investigation. Initial
template is **minimal** — just enough to give the user something to
edit.

**v1 template** (`src/workspace_app/rca/templates/default/`):
- `brief.md` — Investigation Brief skeleton with `{title}` /
  `{severity}` / `{line}` / `{product}` / `{description}` substituted
  in at create time.

That's it for the absolute minimum. Add `report.md` (empty D1–D8
skeleton) once `ReportVersion.generate` is wired so the first version
has somewhere to go.

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
- `src/workspace_app/rca/templates/default/brief.md`
- `src/workspace_app/rca/templates/__init__.py` — `seed_investigation()`
- `src/workspace_app/api/app.py` — wire seed call into create path
- Tests cover field substitution + that brief.md lands in FileStore

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

## 8. Agent surface — generic + RCA-domain tools

Existing generic tools stay (`exec`, `read_file`, `write_file`, `ls`,
`exists`, `delete_file`). New domain tools are added but **all mock**:

| Tool | Returns |
|---|---|
| `spc_read(probe: str, window: str)` | A canned DataFrame-like dict for the probe (e.g., "reflow.zone3" returns the zone-3 drift fixture from the design) |
| `defects_aoi(machine: str, lot: str)` | Defect-list fixture |
| `correlate_find(target, window, candidates, min_r)` | Hard-coded correlation results pointing at the design's narrative (zone-3 → void rate) |
| `pareto_build(window, group_by)` | Pareto bins fixture |
| `fishbone_draft(effect)` | 6M skeleton as JSON |
| `fivewhy_draft(observation)` | 5-Why chain skeleton |
| `report_generate(investigation_id)` | Full 8D markdown — also kicks off `ReportVersion.generate` server-side |

All implemented in `src/workspace_app/rca/tools/` as `@function_tool`
async functions; data comes from `src/workspace_app/rca/fixtures/*.json`.

Per Q9 clarification: there is **no** `rca` Python package inside
notebook cells. Cells use stdlib + numpy/pandas/matplotlib directly,
with inline mock data or fixture loading from FileStore. The agent
tools above run *in the backend process*, not inside notebooks.

**Files:**
- `src/workspace_app/rca/tools/{spc,defects,correlate,pareto,fishbone,fivewhy,report}.py`
- `src/workspace_app/rca/fixtures/*.json`
- Tests for each tool's contract (input/output shape) + that fixtures load

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
| `GET /investigations/{id}/reports` | list report versions | ⏳ §4 |
| `POST /investigations/{id}/reports/generate` | new version | ⏳ §4 |
| `GET /investigations/{id}/reports/{v}` | one version | ⏳ §4 |

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
- **Mock data lives in `rca/fixtures/`, not inlined in tool code.**
  Easier to swap to real adapters later.
- **Tests first via `/tdd`.** Red→green vertical-slice.
- **Honesty over scope creep.** Split + update this doc rather than
  letting items sprawl.

---

## 12. Order of work

1. **Schema rename** (§3) — Workspace → Investigation, fields,
   registry/route renames. Cleanup before any new work goes in.
2. **Template seeding** (§5) — `brief.md` skeleton + create-flow wiring.
3. **Idle-bump + manual close** (§6) — small, lands before kernel work
   adds to the lifecycle complexity.
4. **`PUT /files/{path}`** (§7.4) — small, unblocks FE notebook save.
5. **Notebook execution stack** (§7.1 → §7.5) — biggest chunk:
   - 7.5 sandbox image first (Dockerfile + build target)
   - 7.1 Sandbox.expose_port (Mock + Local; Docker can come right after)
   - 7.2 KernelService (start/shutdown/execute_cell + tests)
   - 7.3 Cell SSE endpoint + CellEvent types
6. **Domain agent tools** (§8) — mock data + tool wrappers + tests.
7. **ReportVersion** (§4) — generate / list / supersede semantics.

Lower priority / deferred to v1.5:
- `run_cell` agent tool (Q6).
- `SandboxKilledIdle` (still needs registry refactor).
- Reconnect endpoint (`GET /investigations/{id}/events?since=`).
- AdapterVolumeFileStore (still nobody asked).
- 5-Why structured editor backend support.
- Fishbone `.canvas` schema + editor backend support.
