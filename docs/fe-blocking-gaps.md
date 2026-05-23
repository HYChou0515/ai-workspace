# FE → BE wiring: resolved

Snapshot: 2026-05-23. All P0–P3 items from the original FE-agent report
(see `git log` for the prior revision of this file) have shipped.
`contract.md` is the single source of truth going forward; every row in
§2 is now `✅` and the FE renders against the live BE without mocks.

The notes below are kept as a wiring summary so future agents can find
the moving pieces without re-deriving them from commits.

---

## Resolved items

| Concern | Resolution | Commit / file |
|---|---|---|
| `POST /investigations/{id}/messages` not in `/openapi.json` | `spec.apply(app)` cached the schema *before* the custom routes were registered. `create_app` now calls `spec.openapi(app)` a second time after every route is registered. | `src/workspace_app/api/app.py` |
| `DELETE /investigations/{id}/messages/current` | Already shipped on the BE; the FE now fires the DELETE alongside the local `AbortController.abort` so the agent loop tears down promptly. | `web/src/hooks/useAgent.tsx`, `web/src/api/real.ts` |
| Files API (`GET / PUT /investigations/{id}/files[...]`) | Shipped (Phase 5). | `src/workspace_app/api/app.py`, `tests/api/test_messages.py` |
| Template seed on `POST /investigation` | Shipped (Phase 2). Includes `/brief.md`, `/drift.ipynb`, `/pareto.ipynb`, `/fishbone.canvas`, `/5-why.md`, `/report.v1.md`, `/data/reflow.zone3.sample.csv`. | `src/workspace_app/rca/templates/default/` |
| `/report.md` vs `/report.v1.md` | Renamed the seed file so the FE's six initial design tabs all open. | `src/workspace_app/rca/templates/default/report.v1.md` |
| Notebook cell execute (`POST/DELETE .../cells/{idx}/execute`) | Shipped (Phase 9). FE's `interruptCell` now also fires the DELETE to actually stop the kernel. | `src/workspace_app/api/app.py`, `web/src/renderers/notebook/NotebookRenderer.tsx` |
| `POST .../kernel/restart` | Shipped (Phase 9). Exposed via `api.restartKernel`; UI affordance can be added once a kernel-status pill is wired. | `src/workspace_app/api/app.py`, `web/src/api/real.ts` |
| `POST /investigations/{id}/close` | Shipped (Phase 4). FE now has a **Close** dropdown in the top bar (Resolved / Abandoned) that calls the endpoint and navigates back to Home. | `src/workspace_app/api/app.py`, `web/src/pages/investigation/InvestigationShell.tsx` |
| SPA bundle delivery | The FE source lives in this repo's `web/`. `web/dist/` is the build output; FastAPI's `StaticFiles` mount auto-picks it up. Build script: `cd web && pnpm run build`. | `vite.config.ts`, `src/workspace_app/api/app.py` |

---

## Known polish items (not blocking)

- **`/-workspacefiles` leaks 19 routes into `/openapi.json`**. Source: `SpecstarFileStore.__init__` registers an internal `_WorkspaceFiles` storage model with specstar, which auto-emits CRUD routes. The FE doesn't call any of them. Cleanup options: rename the internal struct or have specstar grow a "register-for-storage-only, no routes" flag.
- **Restart kernel button** in the notebook status bar. `api.restartKernel({investigationId, notebookPath})` is wired; just needs a button.
- **Upload file UI** — composer attachment uploads to `/uploads/<filename>` were stubbed out at `InvestigationShell.tsx:565`. `api.writeFile` is the underlying call.

---

## How to verify

```bash
# Backend
uv run python -m workspace_app   # 127.0.0.1:8000

# Frontend (only needed when iterating; production reads from web/dist)
cd web && pnpm run dev           # 5173 with proxy to :8000
```

Curl smoke:
```bash
curl -s -X POST http://127.0.0.1:8000/investigation \
  -H 'content-type: application/json' \
  -d '{"title":"smoke","owner":"default-user"}'
# -> { resource_id: "investigation:<uuid>", ... }

curl -s "http://127.0.0.1:8000/investigations/<urlencoded-id>/files"
# -> [ {path: "/brief.md", size: ...}, ..., {path: "/report.v1.md", ...} ]
```
