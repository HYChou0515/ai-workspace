# FE → BE: blocking gaps

Snapshot: 2026-05-23. Author: FE agent. Reference: [`contract.md`](./contract.md).

The RCA 3.0 FE is implemented end-to-end (see `plan-frontend.md` §12.1–§12.10) and
fully usable against the mock API (`VITE_USE_MOCK=1`). Against the live BE today
the shell renders but most workflows are stubbed because the **custom (non-specstar)
routes are not yet shipped**.

This doc lists every wire-level gap the FE needs the BE to close, in priority
order. Each item is anchored to `contract.md` so we don't drift.

---

## P0 — agent chat (blocks the core UX)

Without these the right-hand "RCA Agent" panel can hydrate from `/conversation`
but cannot accept a turn — clicking **Send** today errors out against the real
BE.

### `POST /investigations/{id}/messages`
- Contract: `contract.md` §2.2.
- Status: **not in `/openapi.json`** as of 2026-05-23.
- Request body: `{"content": "string"}`.
- Response: `text/event-stream`. Each event is a single `data: { … }\n\n` line
  per the SSE framing in `contract.md` §3.3.
- Event union: `AgentEvent` (see `contract.md` §3.1 / `web/src/events.ts`):
  - `message_delta` `{type, text, reasoning?: boolean}` — non-terminal
  - `tool_start` `{type, call_id, name, args}` — non-terminal
  - `tool_end` `{type, call_id, output}` — non-terminal
  - `tool_call_parse_error` `{type, hint, call_id?, raw?}` — non-terminal
  - `sandbox_killed_idle` `{type}` — non-terminal (deferred, FE tolerates it)
  - `max_turns_exceeded` `{type, turns}` — **terminal**
  - `done` `{type}` — **terminal**
  - `error` `{type, message}` — **terminal**
  - `run_cancelled` `{type}` — **terminal**
- Side-effect: append the user message + agent reply to the resource's
  Conversation so `GET /conversation` returns the full history on reload.

### `DELETE /investigations/{id}/messages/current`
- Contract: `contract.md` §2.2.
- Status: not shipped.
- Purpose: interrupt the in-flight turn. The current stream should close with
  a final `run_cancelled` event.
- FE behaviour today: an `AbortController` aborts the `fetch`, which the BE
  must observe; the DELETE is the canonical "cancel" signal regardless.

---

## P1 — workspace files (everything in the editor tab is dead without these)

Without these every renderer (`MarkdownRenderer`, `NotebookRenderer`,
`ReportRenderer`, `FishboneRenderer`) shows "loading…" forever, and the
attach button + autosave both fail.

### `GET /investigations/{id}/files[?prefix=<p>]`
- Contract: `contract.md` §2.3.
- Status: not shipped (the only `*files*` paths in `/openapi.json` are the
  legacy `/-workspacefiles/*` specstar resource — see "Pre-pivot leftover"
  below).
- Response: `[{ "path": "/brief.md", "size": 1234 }, …]`.
- Optional `?prefix=` to filter (the report renderer uses
  `?prefix=/report.v` to derive versions).
- FE soft-handles 404 → empty list today, so the workspace shell still
  renders an empty file tree; **but no file content is reachable**.

### `GET /investigations/{id}/files/{path:path}`
- Contract: `contract.md` §2.3.
- Status: not shipped.
- Response: raw bytes. `Content-Type: text/plain` for text, `application/octet-stream`
  for binary. FE branches on the content-type prefix:
  `text/`, `application/json`, `application/xml` → text body; otherwise blob.

### `PUT /investigations/{id}/files/{path:path}`
- Contract: `contract.md` §2.3.
- Status: not shipped.
- Body: raw bytes (string today; FE may later send Blob for binary uploads).
- Status: 204 on success, 4xx with body for errors.
- Used by: markdown edit-mode autosave (debounced 500 ms), notebook save on
  `cell_done`, composer attach upload to `/uploads/<filename>`.

### Template-seed on `POST /investigation`
- Contract: `contract.md` §2.1 marks this **custom-wrapped**: "create + seed
  default template files + start at TRIAGING".
- Status: today it's the specstar default create. No `/brief.md`,
  `/drift.ipynb`, `/pareto.ipynb`, `/fishbone.canvas`, `/5-why.md`,
  `/report.v1.md`, or `/data/*.csv` is seeded.
- Result: the workspace tabs (which the design ships with all six views
  pre-open) all show "Loading…" or empty state until the user asks the agent
  to write something.

---

## P2 — notebook execution (blocks the F8 cell run UX)

Notebook viewer parses and renders `.ipynb` JSON client-side OK; you can
view a notebook the agent wrote. **Run cell** errors out today.

### `POST /investigations/{id}/notebooks/{path}/cells/{idx}/execute`
- Contract: `contract.md` §2.4.
- Status: not shipped.
- Request body: `{"code": "string"}`.
- Response: `text/event-stream` of `CellEvent` (contract.md §3.2):
  - `cell_stream` `{type, stream: "stdout" | "stderr", text}` — non-terminal
  - `cell_display_data` `{type, data: {<mime>: string, …}}` — non-terminal,
    where the FE picks a single mime per `image/png > text/html > text/plain`
  - `cell_error` `{type, ename, evalue, traceback}` — non-terminal
  - `cell_done` `{type, execution_count}` — **terminal**

### `DELETE /investigations/{id}/notebooks/{path}/cells/{idx}/execute`
- Contract: `contract.md` §2.4.
- Status: not shipped.
- Purpose: interrupt cell run.

### `POST /investigations/{id}/notebooks/{path}/kernel/restart`
- Contract: `contract.md` §2.4.
- Status: not shipped.
- Response: 204.
- FE today renders a static `kernel py3.11 idle` pill — when this route lands
  the FE will wire a Restart Kernel button + live kernel status.

---

## P3 — lifecycle action (blocks the close flow)

### `POST /investigations/{id}/close`
- Contract: `contract.md` §2.1.
- Status: not shipped.
- Request body: `{"status": "resolved" | "abandoned"}`.
- Side-effect: tears the sandbox down; transitions the Investigation status.
- FE today has no "Close investigation" affordance — could add once route lands.

---

## Wire-format observations (already adapted on the FE)

These aren't gaps per se but worth knowing — `real.ts` has been written
against the current specstar shape (verified via curl against `/investigation`
on 2026-05-23):

| Endpoint | Envelope on the wire | Notes |
|---|---|---|
| `GET /investigation` | `[ {data, revision_info, meta}, … ]` | Bare array, not `{data: […]}`. |
| `GET /investigation/{id}` | `{data, revision_info, meta}` | Same shape per item. |
| `POST /investigation` | `{resource_id, uid, revision_id, created_time, updated_time, created_by, updated_by}` | **Metadata only — no `data` field.** FE has to do a follow-up GET to obtain the full record. If the create handler can return the full envelope, drop the second round-trip. |
| `GET /conversation` | Same array-of-envelopes shape. | |

`revision_info.resource_id` is the canonical id (e.g. `investigation:<uuid>`).
The FE format helper `formatInvestigationId` strips this prefix and renders
`INC-<UUID first 8 hex>` for display.

---

## Pre-pivot leftover: `/-workspacefiles` specstar resource

`/openapi.json` still exposes `/-workspacefiles/*` (~19 paths). The contract
doesn't define this resource any more — files now belong to the bespoke
`/investigations/{id}/files` routes above. Suggestion: deregister the
`WorkspaceFiles` resource from `register_all(spec)` to keep the OpenAPI
surface clean and avoid confusion with the new file routes once they land.

---

## SPA mount observation

`web/src/workspace_app/api/app.py` mounts `web/dist` via FastAPI's
`StaticFiles` — that's correct. But on 2026-05-23 the BE process listening on
`127.0.0.1:8000` is rooted at **`/home/hychou/project/kb/ai-workspace/`**, not
this repo. As a result the served HTML is an old `<title>Workspace</title>`
build from the parent project. If you intend the BE to serve the RCA 3.0 FE:

```bash
# from the RCA-FE repo root
cd /home/hychou/project/kb/ai-workspace-fe
cd web && pnpm run build && cd ..
uv run python -m workspace_app
```

Then the served `/` will be `<title>RCA · 3.0</title>` and `/assets/`
will contain the latest hashed bundle.

---

## How to verify after BE patches land

- `GET /openapi.json` → expect `/investigations/{id}/messages`,
  `/investigations/{id}/files`, `/investigations/{id}/files/{path:path}`,
  `/investigations/{id}/notebooks/{path}/cells/{idx}/execute`,
  `/investigations/{id}/notebooks/{path}/kernel/restart`,
  `/investigations/{id}/close` in the path list.
- `curl -X POST http://127.0.0.1:8000/investigation -d '{"title":"smoke","owner":"default-user", …}'`
  followed by `GET /investigations/{id}/files` → expect the seeded template
  files (`/brief.md`, …) in the response array.
- Drop `VITE_USE_MOCK=1` and reload the SPA — Home loads from real BE, click
  through to a freshly-created investigation, the six design tabs open with
  real content, Send a chat message → see streamed `message_delta` events,
  open `/drift.ipynb` → click ▶ → see streamed `cell_stream` events.

Each item lands independently; FE can verify them one at a time as they
ship — please bump the corresponding row in `contract.md` from `⏳` to `✅`
in the same commit so we don't drift.
