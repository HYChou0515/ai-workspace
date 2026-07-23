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
import { checkVersionHeader } from "../lib/versionSkew";

export const API_BASE = import.meta.env.BASE_URL.replace(/\/+$/, "");

// Root of every backend URL (#177). "" + "/api" → "/api"; "/sub" + "/api" → "/sub/api".
export const API_PREFIX = `${API_BASE}/api`;

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const resp = await fetch(API_PREFIX + path, init);
  // Version-skew handshake: a stale cached bundle against a newer api reloads
  // itself at a safe moment (the v2026.07.23 incident). Passive — reads one
  // header, never blocks or fails the call.
  checkVersionHeader(resp);
  return resp;
}

/**
 * A failed backend response, carrying its HTTP status.
 *
 * The status is load-bearing, not decoration: the chat send path treats
 * 502/503/504 as "an idle gateway cut the request while the turn keeps running"
 * and stays in the streaming state so the stream / store-poll can still surface
 * the reply. A client that throws a bare `Error` silently opts out of that —
 * which is how the WorkItem chat came to show a hard "send failed: 504" while
 * the answer streamed in underneath it. Every client throws this one.
 */
export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "HttpError";
  }
}
