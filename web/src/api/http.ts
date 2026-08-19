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
    /**
     * The server's machine-readable reason, when it sent one
     * (`{"detail": {"error": "..."}}`).
     *
     * A status alone is not always enough to say what happened: three different
     * limits answer 507 — this workspace is full, YOUR total across every item
     * is full, and you are holding as many live environments as you may. They
     * need three different remedies (delete here / delete somewhere else /
     * close something), and the backend deliberately distinguishes them. Without
     * carrying the code, the UI can only guess, and its guess sends people to
     * look in the wrong place.
     */
    public code?: string,
    /** Other limits the SAME refusal named — see `errorInfo`. */
    public also?: string[],
    /** The structured error body, when there was one — carries the numbers. */
    public detail?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "HttpError";
  }
}

/**
 * Build an `HttpError` from a failed response — code included, always.
 *
 * The code is what lets the UI say something a person can act on, and it only
 * ever arrives if the throw site remembers to ask for it. It did not: the
 * item-level send passed it and the chat-scoped send did not, so the identity
 * refusal #714 added rendered as "send failed: 500" on the surface every
 * composer actually uses. Attaching it at each `throw new HttpError(...)` is the
 * kind of rule that is obeyed on the day it is written and forgotten on the
 * next call site, so it lives here instead.
 *
 * Safe on any response: `errorCode` clones before reading and swallows a body
 * that is not JSON, so callers keep whatever message they already wrote.
 */
export async function httpErrorFrom(resp: Response, message: string): Promise<HttpError> {
  return new HttpError(resp.status, message, await errorCode(resp));
}

/** Read the structured error code out of a JSON error body, if there is one. */
export async function errorCode(resp: Response): Promise<string | undefined> {
  return (await errorInfo(resp)).code;
}

/**
 * The error code AND any other limits the same refusal named (`also`).
 *
 * A turn is gated on more than one rule and can be refused by several at once.
 * Reporting only the first produced a sequence that reads as a bug: free disk,
 * resend, get told about a different limit.
 */
export async function errorInfo(
  resp: Response,
): Promise<{ code?: string; also?: string[]; detail?: Record<string, unknown> }> {
  try {
    const body = await resp.clone().json();
    const detail = body?.detail;
    const code = typeof detail?.error === "string" ? detail.error : undefined;
    const also = Array.isArray(detail?.also)
      ? detail.also.filter((c: unknown): c is string => typeof c === "string")
      : undefined;
    // The whole object, not just the code: a quota refusal carries the numbers
    // behind it, and a message that names a limit without one cannot be checked
    // by the person reading it.
    return { code, also, detail: detail && typeof detail === "object" ? detail : undefined };
  } catch {
    return {}; // not JSON, or no body — the status is all we have
  }
}
