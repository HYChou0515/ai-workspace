# RCA 3.0 — Wire Contract

**Single source of truth** for the FE ↔ BE boundary. Anyone changing
the wire (data models, routes, SSE events) updates this doc in the
same commit, and both `plan-backend.md` + `plan-frontend.md` reference
back here.

Architectural posture: **the backend is RCA-agnostic.** It stores
`Investigation` metadata + conversation history, runs the agent + the
sandbox, and serves files. All RCA-specific structure (5-Why,
fishbone, 8D report sections, hypothesis cells, corrective actions…)
is **data the agent writes into the FileStore as plain `.md` /
`.ipynb` / `.csv` / `.json` / `.canvas` files**. FE renderers detect
file types by extension and apply the right renderer.

---

## 1. specstar models

Three resources registered via `register_all(spec)`. specstar
auto-generates REST routes (`/investigation`, `/agent-config`,
`/conversation`) and auto-adds metadata (`resource_id`,
`created_time`, `updated_time`, `created_by`, `updated_by`).

### 1.1 `Investigation`

```python
from enum import StrEnum
from msgspec import Struct, field


class Severity(StrEnum):
    P0 = "P0"   # halt
    P1 = "P1"   # critical
    P2 = "P2"   # major
    P3 = "P3"   # minor
    P4 = "P4"   # cosmetic


class Status(StrEnum):
    """Investigation status flow.
       create → TRIAGING → AWAITING_REVIEW → RESOLVED  (happy path)
                                          └→ ABANDONED  (closed without RC)
    """
    TRIAGING = "triaging"
    AWAITING_REVIEW = "awaiting_review"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class Investigation(Struct):
    title: str                                            # required
    description: str = ""                                 # multi-line; design's "initial brief"
    severity: Severity = Severity.P2
    status: Status = Status.TRIAGING
    product: str = ""                                     # part / board (e.g. "MX-7 board")
    owner: str = "default-user"                           # user id; resolved via company API
    members: list[str] = field(default_factory=list)      # additional user ids
    topics: list[str] = field(default_factory=list)       # free-form tags ("Reflow zone-3", ...)
    attached_agent_config_id: str | None = None           # which AgentConfig to use
```

### 1.2 `Conversation`

```python
class Message(Struct):
    role: str                                    # user / assistant / tool / system
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None


class Conversation(Struct):
    investigation_id: str                        # was workspace_id in workspace-app
    messages: list[Message] = field(default_factory=list)
```

### 1.3 `AgentConfig`

```python
class AgentConfig(Struct):
    name: str
    model: str = "ollama_chat/qwen3:14b"
    system_prompt: str = ""                                 # RCA prompt loaded here
    allowed_tools: list[str] = field(default_factory=list)  # subset; empty = all
    env: dict[str, str] = field(default_factory=dict)
    sandbox_image: str = "workspace-app/sandbox:py312-ds"
    idle_timeout_seconds: int = 28800                       # 8 hours
```

### 1.4 Fields shown in the design but NOT stored on the model

The design displays many surface fields the BE doesn't persist; FE
derives them from the truth above + sibling state.

| Design surface | Where it comes from |
|---|---|
| `INC-2026-0142` | specstar `resource_id`; FE formats the display string |
| `summary` (2nd line of table row) | first sentence/line of `description`; FE-derived |
| `sevTone` / `statusTone` (colors) | FE color-mapping constants |
| `updated` ("12 min ago") | specstar `updated_time` + FE relative-format |
| `agent: "running" \| "idle"` | true iff `session.current_turn` is alive in BE registry |
| `pinned` | client-side `localStorage` preference (no BE storage) |
| `lot` | dropped — appears in agent narration / notebook code as plain text only |
| `reportV` / `reportProgress` | derived from `/report.v*.md` file listing + agent run state |

---

## 2. HTTP routes

All paths are JSON unless noted. Auth: every request implicitly runs
as `default-user` in v1 (no header, no token). When real auth lands
this section adds an `Authorization:` requirement.

### 2.1 Investigation lifecycle

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET`    | `/investigation`                       | list investigations (specstar auto) | ⏳ rename |
| `POST`   | `/investigation`                       | **custom-wrapped:** create + seed default template files + start at TRIAGING | ⏳ §3, §5 |
| `GET`    | `/investigation/{id}`                  | get one (specstar auto) | ⏳ rename |
| `PATCH`  | `/investigation/{id}`                  | partial update (specstar auto) | ⏳ rename |
| `DELETE` | `/investigation/{id}`                  | soft-delete (specstar auto) | ⏳ rename |
| `POST`   | `/investigations/{id}/close`           | manual close: `{"status": "resolved" \| "abandoned"}` → tears sandbox down | ⏳ §6 |

### 2.2 Chat / agent turn

| Method | Path | Purpose | Status |
|---|---|---|---|
| `POST`   | `/investigations/{id}/messages`            | send a user message → SSE stream of `AgentEvent` | ⏳ rename |
| `DELETE` | `/investigations/{id}/messages/current`    | interrupt the in-flight turn (RunCancelled goes to old stream) | ⏳ rename |

POST body shape:
```json
{ "content": "string" }
```

### 2.3 Files

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET`    | `/investigations/{id}/files[?prefix=<p>]` | list files: `[{"path", "size"}]`           | ⏳ rename |
| `GET`    | `/investigations/{id}/files/{path:path}`  | read file body (text/plain or octet-stream) | ⏳ rename |
| `PUT`    | `/investigations/{id}/files/{path:path}`  | write raw bytes (FE auto-saves notebooks here) | ⏳ §7.4 |

### 2.4 Notebook execution

| Method | Path | Purpose | Status |
|---|---|---|---|
| `POST`   | `/investigations/{id}/notebooks/{path}/cells/{idx}/execute` | run cell: body `{"code": "string"}` → SSE stream of `CellEvent` | ⏳ §7.3 |
| `DELETE` | `/investigations/{id}/notebooks/{path}/cells/{idx}/execute` | interrupt cell                                | ⏳ §7.3 |
| `POST`   | `/investigations/{id}/notebooks/{path}/kernel/restart`      | restart per-notebook kernel → 204             | ⏳ §7.3 |

### 2.5 Specstar admin (auto-generated, behind `/docs`)

specstar emits ~30 routes per registered resource (CRUD + meta + blobs
+ revisions + search). FE uses only the handful listed above. The
auto-generated routes are still callable for admin/debug; visible at
`GET /openapi.json` and the interactive Swagger UI at `GET /docs`.

### 2.6 Reports — no dedicated endpoints

Reports use the file-naming convention `/report.v{N}.md`:
- Agent writes `/report.v1.md`, `/report.v2.md`, … via `write_file`.
- FE lists `/report.v*.md` via `GET /investigations/{id}/files?prefix=/report.v`.
- The highest N is **current**; others are **superseded**.
- "Generate new version" is just an agent chat prompt — agent writes
  the next `/report.v{N+1}.md`. No special endpoint.

### 2.7 RCA-domain agent tools — none

No `spc_read`, `defects_aoi`, `pareto_build`, etc. routes. The agent
uses only the generic tools (`exec`, `read_file`, `write_file`, `ls`,
`exists`, `delete_file`), and its system prompt teaches it the RCA
workflow / file conventions. Mock SPC / AOI data lives as CSV
fixtures inside the seeded template (`/data/*.csv`).

---

## 3. SSE event types

Two **separate** event unions are streamed over two different
endpoints. Both serialize one JSON object per `data:` line.

### 3.1 `AgentEvent` — over `POST /investigations/{id}/messages`

Mirrored in `web/src/events.ts`.

| Variant | Shape | Terminal? | Notes |
|---|---|---|---|
| `MessageDelta`        | `{type: "message_delta", text: string}` | no | append to assistant message |
| `ToolStart`           | `{type: "tool_start", call_id: string, name: string, args: object}` | no | |
| `ToolEnd`             | `{type: "tool_end", call_id: string, output: string}` | no | |
| `RunDone`             | `{type: "done"}` | **yes** | normal completion |
| `RunError`            | `{type: "error", message: string}` | yes | catch-all failure |
| `RunCancelled`        | `{type: "run_cancelled"}` | yes | user interrupted (DELETE or new POST) |
| `ToolCallParseError`  | `{type: "tool_call_parse_error", hint: string, call_id: string?, raw: string?}` | no | retry-with-feedback follows |
| `MaxTurnsExceeded`    | `{type: "max_turns_exceeded", turns: number}` | yes | agent didn't converge |

Deferred (declared in FE for future use but not emitted yet):
- `SandboxKilledIdle` `{type: "sandbox_killed_idle"}` — needs registry refactor.

### 3.2 `CellEvent` — over `POST /investigations/{id}/notebooks/{path}/cells/{idx}/execute`

To land with §7.3 of plan-backend.

| Variant | Shape | Terminal? | Notes |
|---|---|---|---|
| `CellStream`       | `{type: "cell_stream", stream: "stdout" \| "stderr", text: string}` | no | append to cell output |
| `CellDisplayData`  | `{type: "cell_display_data", data: {<mime>: string, ...}}` | no | mime bundle: `image/png` base64, `text/html`, `text/plain` |
| `CellError`        | `{type: "cell_error", ename: string, evalue: string, traceback: string[]}` | no | rendered red |
| `CellDone`         | `{type: "cell_done", execution_count: number}` | **yes** | finalizes cell + closes stream |

### 3.3 SSE framing

Standard `text/event-stream`. Each event is a single `data:` line
followed by a blank line:

```
data: {"type":"message_delta","text":"hello"}

data: {"type":"done"}

```

No `event:` or `id:` lines for v1. (Reconnect / `Last-Event-ID` is
deferred.)

---

## 4. Brand & static

| Path | Source | Use |
|---|---|---|
| `/rca-mark.svg`           | `design_handoff_rca_3.0/assets/rca-mark.svg`           | primary mark, light bg |
| `/rca-mark-light.svg`     | `design_handoff_rca_3.0/assets/rca-mark-light.svg`     | mark on dark bg |
| `/rca-logo-horizontal.svg`| `design_handoff_rca_3.0/assets/rca-logo-horizontal.svg`| full lockup |
| `/favicon.ico`            | `design_handoff_rca_3.0/assets/favicon.ico`            | tab icon |

FE copies these into `web/public/` at build time; backend serves
whatever's in `web/dist/` via the existing SPA static mount.
**The orange dot at the mark's apex must remain — it's the brand.**

---

## 5. File conventions the agent honors (FE renderers depend on)

These are agent-side conventions backed by the RCA system prompt
(plan-backend §8). The BE doesn't enforce them; the FE renderers
match on extension + content shape:

| File path / pattern | Content shape | Renderer |
|---|---|---|
| `/brief.md`         | Markdown — "Investigation Brief" sections | F10 markdown |
| `/drift.ipynb`      | nbformat v4 JSON                          | F8 notebook |
| `/pareto.ipynb`     | nbformat v4 JSON                          | F8 notebook |
| `/fishbone.canvas`  | JSON: `{effect: string, branches: [{label, side, items: [{t, strong?}]}]}` | F12 fishbone SVG |
| `/5-why.md`         | Markdown with `## Why #N` headings        | F10 markdown (v1.5 may add structured JSON variant) |
| `/report.v{N}.md`   | Markdown D1–D8 (8D)                       | F11 report (picks max N as current) |
| `/data/*.csv`       | Sample fixture data (seeded by template)  | (not viewed; consumed by notebook code) |

---

## 6. Status legend

- ✅ shipped (committed and tested)
- ⏳ planned, not yet shipped — section reference in `plan-backend.md`
- ⏸ deferred — not in v1, explicit reason in `plan-backend.md` §2

When status changes, update this doc + the cross-cutting section in
`plan-backend.md` in the same commit.
