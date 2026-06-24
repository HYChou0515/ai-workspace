# Plan — #177: URLs with `:` break on refresh (FE client-route ↔ API-route namespace collision)

## Symptom (as reported)

> URL 出現 `:` 會爛掉，我們的 id 常會有 `:` 在裡面。先按上一頁，再 refresh 就會變成 JSON（可能導去 backend 了）。

## Root cause (confirmed by route-table comparison — NOT the colon)

The colon is a red herring. The real cause: **frontend client-side routes and backend
API routes share the same `/kb/...` URL namespace.** Backend routes are registered before
the SPA mount, so on a hard refresh (browser issues a `GET`) the API route matches first and
returns JSON instead of the SPA's `index.html`.

Exact collisions (FE client route ↔ backend `GET`):

| FE client route | backend GET route | file |
| --- | --- | --- |
| `/kb/collections` | `GET /kb/collections` | `api/kb_routes.py:351` |
| `/kb/collections/:cid/documents` (bare) | `GET /kb/collections/{id}/documents` | `api/kb_routes.py:654` |
| `/kb/collections/:cid/wiki` (+`/page`,`/status`) | `GET /kb/collections/{id}/wiki…` | `api/kb_routes.py:542/549/610` |
| `/kb/chats` | `GET /kb/chats` | `api/kb_chat_routes.py:296` |
| `/kb/chats/:chatId` | `GET /kb/chats/{chat_id}` | `api/kb_chat_routes.py:322` |

Why it *looks* colon-specific & why "back then refresh":
- chat ids are always `conversation:<uuid>`, so every breaking case happens to carry a colon.
- `/kb/chats/:chatId` only appears in the address bar after you click into a chat (client-side
  nav); "上一頁" puts that URL back in the bar, "refresh" finally does a real `GET` → JSON.
- Dev makes it blunt: `web/vite.config.ts` proxies `/kb` → `:8000`, so refreshing any `/kb/...`
  on `:5173` is proxied to the backend.

Counterexample that already works: `/a/...` never collides — the App routes deliberately
separate client (`/a/{slug}/{itemId}`) from API (`/a/{slug}/items/{itemId}`, `/a/{slug}/profiles`)
with a literal segment. Only #93's KB routes reused the KB REST paths verbatim.

## Locked decisions (grill-me)

1. **Global `/api` prefix.** All backend routes (hand-written + specstar) move under `/api`,
   via `APIRouter(prefix="/api")` + `app.include_router(...)` — **not** a sub-app mount.
   Rationale: a router keeps `request.app.state` (5 coordinators + turn_engine +
   workflow_orchestrator), the single `lifespan`, and one `/api/openapi.json` intact; a mounted
   sub-app would not run its own lifespan (Starlette gotcha). specstar's `apply()` natively
   supports `router=` (its docstring example 2/3), so `spec.apply(app, router=api_router,
   auto_include=False)` puts CRUD routes on the prefixed router too.
2. **Docs move too:** `/api/docs`, `/api/openapi.json`, `/api/redoc`, and
   `/api/docs/oauth2-redirect` (set all four on the `FastAPI(...)` constructor). `spec.openapi(app)`
   still customises the schema content.
3. **Clean switch, no backward-compat aliases.** Pre-1.0 / local / unpushed; external callers
   (if any) move to `/api` too.
4. **Tests:** `Harness.client` becomes an `ApiClient` wrapper that auto-prepends `/api` to
   relative paths → the **308 existing call sites stay unchanged**. Add `Harness.spa_client`
   (raw `TestClient`, no prefix) for SPA-fallback / openapi / the regression tests.
5. **Frontend:** introduce `API_PREFIX = API_BASE + "/api"` in `web/src/api/http.ts`.
   `apiFetch` and every **direct API URL builder** use `API_PREFIX`; only genuine **SPA-route
   hrefs** keep `API_BASE`.
   - → `API_PREFIX`: `apiFetch` (http.ts), download href (`kb.ts:453`), source-doc blob
     (`kbFileService.ts:229`), `/blobs/{id}` (`kbLinks.ts:58`), workspace file
     (`fileService.ts:92`), BE-emitted markdown URLs (`mdUrlTransform.ts:18`, BE keeps emitting
     root-relative `/blobs/…`; FE prepends).
   - → stays `API_BASE`: `kbLinks.ts:50` `docHref` (links to the SPA page `/kb/doc/...`).
   - SSE already flows through `apiFetch` (real.ts:420, itemChats.ts:141, monitor.ts:119) → free.
   - No raw `fetch("/...")` bypasses `apiFetch` (verified).
6. **SPA fallback guard:** `_SpaStaticFiles` returns a real 404 (no `index.html` fallback) for
   paths starting with `api/`, so an unknown `/api/...` GET yields a JSON-able 404, not HTML.
7. **Guardrail + regression tests** (DoD): a meta-test asserts every `app.routes` entry except
   the SPA mount starts with `/api` (docs/openapi/redoc included); plus regression tests that
   `spa_client.get(<colliding path>)` returns `text/html`, not JSON.
8. **Vite dev proxy:** collapse to a single `{ "/api": "http://localhost:8000" }`; drop the
   stale per-resource entries (`/investigation(s)`, `/conversation`, `/agent-config`, `/kb`).
   Everything else falls back to `index.html` (Vite default) → dev refresh of FE routes works.

## Phases (flat, TDD red→green)

- **P1 — Guardrail + regression tests (RED).** `test_all_backend_routes_under_api` (skip the
  `spa` mount) and `test_browser_refresh_serves_spa` (the colliding paths via `spa_client`).
  Both fail against today's code.
- **P2 — Backend `/api` restructure (→ green for P1).** Build `api_router = APIRouter(prefix="/api")`;
  `spec.apply(app, router=api_router, auto_include=False)`; register every hand-written route on
  `api_router` (broaden `register_*_routes(app: FastAPI | APIRouter, …)` + flip the inline
  `@app.*` closures to `@api_router.*`); `app.include_router(api_router)`; `spec.openapi(app)`.
  Set the four docs URLs on `FastAPI(...)`. Add the `_SpaStaticFiles` `api/` 404 guard. Keep
  lifespan / `app.state` / SPA mount on the outer `app`.
- **P3 — Test harness prefix (→ green for the 308 existing tests).** `ApiClient` wrapper +
  `spa_client` on `Harness` in `tests/api/conftest.py`.
- **P4 — Frontend.** `API_PREFIX` constant; route `apiFetch` + the five direct builders through
  it; `mdUrlTransform` → `API_PREFIX`; keep `docHref` on `API_BASE`. Update the FE tests that
  assert URLs (`http.test.ts`, etc.).
- **P5 — Vite proxy** single `/api`.
- **P6 — Full gate.** Backend: `coverage run -m pytest && coverage combine && coverage report
  --fail-under=100` + `ruff check` + `ruff format --check` + `ty check`. FE: `pnpm typecheck`
  + vitest + `pnpm build`. Live smoke: boot the app, refresh `/kb/chats/<id>` → SPA loads.

## Verify-during-implementation notes

- specstar may emit a `Location` header / HATEOAS links on create — confirm they land under
  `/api` (or that the FE ignores them; the FE builds URLs from ids, so low risk).
- Confirm all `/blobs/…` image URLs in rendered markdown flow through `baseAwareUrlTransform`
  (so the FE-side `/api` prefix reaches them).
- The guardrail must also skip the swagger oauth2-redirect only if it ends up under `/api`
  (it will, once `swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect"`).
