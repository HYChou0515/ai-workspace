/**
 * Same-origin fetch that respects a sub-path deploy. Vite bakes the deploy
 * base into `import.meta.env.BASE_URL` (e.g. "/my-svc/rca/" or "/").
 *
 * #177: the backend lives entirely under `/api`, so the SPA owns the rest of the
 * URL space and a hard-refreshed client route can't collide with an API route.
 * `API_PREFIX` (= deploy base + `/api`) is the root of every BACKEND URL — use it
 * via `apiFetch` for fetches and directly for asset hrefs (blobs, downloads,
 * workspace files). `API_BASE` (deploy base only, no `/api`) is for linking to a
 * client-side SPA route (e.g. `/kb/doc/...`).
 */

// "/my-svc/rca/" → "/my-svc/rca"; "/" → "". SPA-route links only.
export const API_BASE = import.meta.env.BASE_URL.replace(/\/+$/, "");

// Root of every backend URL (#177). "" + "/api" → "/api"; "/sub" + "/api" → "/sub/api".
export const API_PREFIX = `${API_BASE}/api`;

export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(API_PREFIX + path, init);
}
