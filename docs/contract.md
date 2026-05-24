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
    owner: str                                            # required — user id; resolved via company API
    description: str = ""                                 # multi-line; design's "initial brief"
    severity: Severity = Severity.P2
    status: Status = Status.TRIAGING
    product: str = ""                                     # part / board (e.g. "MX-7 board")
    members: list[str] = field(default_factory=list)      # additional user ids
    topics: list[str] = field(default_factory=list)       # free-form tags ("Reflow zone-3", ...)
    attached_agent_config_id: Annotated[
        str | None, Ref("agent_config", on_delete=OnDelete.set_null)
    ] = None
    template_profile: str = "default"                     # which template seeded this investigation
```

`template_profile` records the template the investigation was created
from; it's persisted so the agent's system prompt can be composed with
that template's starting-files appendix at turn time (base prompt +
`rca/templates/{profile}/_prompt.md`).

`owner` has **no default** — every investigation must declare its
creator at create time. The API layer reads the current user (v1:
always `"default-user"`) and fills it; v2 SSO replaces that with a
real user id.

`attached_agent_config_id` is a `Ref` to `AgentConfig`. If the
referenced config is deleted, the field auto-clears to `None` (the
investigation keeps working with whatever default agent the API
factories construct).

### 1.2 `Conversation`

```python
class Message(Struct):
    role: str                                    # user / assistant / tool / system
    content: str
    author: str | None = None                    # user id when role=user;
                                                 # agent name when role=assistant
    reasoning: str | None = None                 # LLM reasoning / thinking content
                                                 # (Qwen3 <thinking>, OpenAI o-series, ...)
    tool_call_id: str | None = None              # role=tool
    tool_name: str | None = None                 # role=tool
    tool_args: dict[str, Any] | None = None      # role=tool — call args (captured from ToolStart)
    created_at: int | None = None                # epoch ms; restores log timestamps on reload


class Conversation(Struct):
    investigation_id: Annotated[
        str, Ref("investigation", on_delete=OnDelete.cascade)
    ]
    messages: list[Message] = field(default_factory=list)
```

`investigation_id` is a `Ref` with `cascade` — deleting the
investigation deletes its conversation along with it.

`Message.author` carries the user id when `role == "user"` (so the
multi-user UI can label "Alice / 14:30:12" vs "Bob / 14:31:05") and
the agent identifier when `role == "assistant"` (forward-compatible
with multi-agent setups; v1 it's just the active `AgentConfig.name`).

`Message.reasoning` separates the model's chain-of-thought from
`content`. Qwen3 returns `thinking` as a sibling field; OpenAI's
o-series returns reasoning items; our runner consolidates both into
this single field. FE can render it collapsed (ChatGPT-style "Show
thinking") without conflating it with the assistant's user-facing
answer.

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
| `GET`    | `/investigation`                       | list investigations (specstar auto) | ✅ |
| `POST`   | `/investigation`                       | **custom-wrapped:** create + seed default template files + start at TRIAGING | ✅ |
| `GET`    | `/investigation/{id}`                  | get one (specstar auto) | ✅ |
| `PATCH`  | `/investigation/{id}`                  | partial update (specstar auto) | ✅ |
| `DELETE` | `/investigation/{id}`                  | soft-delete (specstar auto) | ✅ |
| `POST`   | `/investigations/{id}/close`           | manual close: `{"status": "resolved" \| "abandoned"}` → tears sandbox down | ✅ |

### 2.2 Chat / agent turn

| Method | Path | Purpose | Status |
|---|---|---|---|
| `POST`   | `/investigations/{id}/messages`            | send a user message → SSE stream of `AgentEvent` | ✅ |
| `DELETE` | `/investigations/{id}/messages/current`    | interrupt the in-flight turn (RunCancelled goes to old stream) | ✅ |

POST body shape:
```json
{ "content": "string" }
```

### 2.3 Files

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET`    | `/investigations/{id}/files[?prefix=<p>]` | list files: `[{"path", "size"}]`           | ✅ |
| `GET`    | `/investigations/{id}/dirs`               | directory paths incl. empty ones (for the tree): `[string]` | ✅ |
| `GET`    | `/investigations/{id}/files/{path:path}`  | read file body (text/plain or octet-stream) | ✅ |
| `PUT`    | `/investigations/{id}/files/{path:path}`  | write raw bytes (FE auto-saves notebooks here) → 204 | ✅ |
| `DELETE` | `/investigations/{id}/files/{path:path}`  | delete a file **or** directory subtree → 204 (404 if absent) | ✅ |
| `POST`   | `/investigations/{id}/files/mkdir`        | create empty dir: body `{"path"}` → 204 (409 if a file occupies it) | ✅ |
| `POST`   | `/investigations/{id}/files/move`         | rename/move file or dir subtree: body `{"from", "to"}` → 204 (400 into-self, 404 missing, 409 target exists) | ✅ |
| `POST`   | `/investigations/{id}/files/copy`         | copy file or dir subtree: body `{"from", "to"}` → 204 (same errors as move) | ✅ |

### 2.3b Search / replace (VSCode search panel)

| Method | Path | Purpose | Status |
|---|---|---|---|
| `POST`   | `/investigations/{id}/search`  | full-text search → `[{"path", "matches": [{"line","col","text"}]}]` | ✅ |
| `POST`   | `/investigations/{id}/replace` | search + replace across files → `{"replaced": int}` | ✅ |

Search/replace body (`replace` adds `replacement`):
```json
{ "query": "string", "regex": false, "caseSensitive": false,
  "wholeWord": false, "include": "", "exclude": "", "replacement": "" }
```
Empty `query` → no-op (`[]` / `{"replaced": 0}`); an invalid regex 422s.
Binary (non-UTF-8) files are skipped.

### 2.3c Direct sandbox shell (Terminal pane)

| Method | Path | Purpose | Status |
|---|---|---|---|
| `POST`   | `/investigations/{id}/exec` | run a shell cmd **synchronously**: body `{"cmd": [string]}` → `{exit_code, stdout, stderr}`; empty `cmd` 422s | ✅ |

> Note: this is the **Terminal** pane's one-shot exec (full result on return).
> It is distinct from the agent's `exec` *tool*, which streams stdout live as
> `ToolLog` events during a turn (see §3.1).

### 2.4 Notebook execution

| Method | Path | Purpose | Status |
|---|---|---|---|
| `POST`   | `/investigations/{id}/notebooks/{path}/cells/{idx}/execute` | run cell: body `{"code": "string"}` → SSE stream of `CellEvent` | ✅ |
| `DELETE` | `/investigations/{id}/notebooks/{path}/cells/{idx}/execute` | interrupt cell                                | ✅ |
| `POST`   | `/investigations/{id}/notebooks/{path}/kernel/restart`      | restart per-notebook kernel → 204             | ✅ |

### 2.5 Meta

| Method | Path | Purpose | Status |
|---|---|---|---|
| `GET`    | `/templates` | template profile names for the New Investigation picker | ✅ |
| `GET`    | `/activity`  | recent-activity feed (newest first): `[{ts, kind, text, ref}]` | ✅ |

`POST /investigation` body:
```json
{ "title": "string", "owner": "string", "description": "",
  "severity": "P2", "status": "triaging", "product": "",
  "members": [], "topics": [],
  "attached_agent_config_id": null, "template_profile": "default" }
```
`title` + `owner` required; the rest default as shown. An unknown
`template_profile` 422s. Activity `kind` ∈
`investigation_created | investigation_closed | session_closed |
file_written | file_moved | file_copied | file_deleted |
dir_created | dir_deleted | agent_turn_complete`.

### 2.6 Specstar admin (auto-generated, behind `/docs`)

specstar emits ~30 routes per registered resource (CRUD + meta + blobs
+ revisions + search). FE uses only the handful listed above. The
auto-generated routes are still callable for admin/debug; visible at
`GET /openapi.json` and the interactive Swagger UI at `GET /docs`.

### 2.7 Reports — no dedicated endpoints

Reports use the file-naming convention `/report.v{N}.md`:
- Agent writes `/report.v1.md`, `/report.v2.md`, … via `write_file`.
- FE lists `/report.v*.md` via `GET /investigations/{id}/files?prefix=/report.v`.
- The highest N is **current**; others are **superseded**.
- "Generate new version" is just an agent chat prompt — agent writes
  the next `/report.v{N+1}.md`. No special endpoint.

### 2.8 RCA-domain agent tools — none

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
| `MessageDelta`        | `{type: "message_delta", text: string, reasoning?: boolean}` | no | append to assistant message; if `reasoning=true`, append to the reasoning channel instead of the visible content |
| `ToolStart`           | `{type: "tool_start", call_id: string, name: string, args: object}` | no | |
| `ToolEnd`             | `{type: "tool_end", call_id: string, output: string}` | no | |
| `ToolLog`             | `{type: "tool_log", text: string, call_id: string?}` | no | live stdout chunk from a running tool; empty `call_id` attaches to the latest running call |
| `RunDone`             | `{type: "done"}` | **yes** | normal completion |
| `RunError`            | `{type: "error", message: string}` | yes | catch-all failure |
| `RunCancelled`        | `{type: "run_cancelled"}` | yes | user interrupted (DELETE or new POST) |
| `ToolCallParseError`  | `{type: "tool_call_parse_error", hint: string, call_id: string?, raw: string?}` | no | retry-with-feedback follows |
| `MaxTurnsExceeded`    | `{type: "max_turns_exceeded", turns: number}` | yes | agent didn't converge; `turns` is the runner's configured budget |
| `AgentMetrics`        | `{type: "agent_metrics", phase: "up"\|"down"\|"final", prompt_tokens, completion_tokens, elapsed_ms}` | no | live token telemetry (↑/↓ tok/s); up/down approximate, final is exact usage when reported |

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
