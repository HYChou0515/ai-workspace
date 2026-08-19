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
 * Safe on any response — with ONE precondition: the body must not have been
 * read yet. `clone()` throws on a consumed body and the code comes back
 * `undefined`, silently. So a caller that already did `await resp.text()` must
 * either build the error before reading, or keep passing the code itself; this
 * helper cannot tell the difference and will not warn.
 */
export async function httpErrorFrom(resp: Response, message: string): Promise<HttpError> {
  return new HttpError(resp.status, message, await errorCode(resp));
}

/** How long a failed response's body may take to arrive before we give up on
 *  reading a reason out of it.
 *
 *  Deliberately generous rather than tight. The two failure directions are not
 *  symmetric: too long merely delays a rejection that is still bounded, while
 *  too short discards a code that WAS coming — and a missing code is not a
 *  missing detail, it is the wrong message (see `quotaFailure`: three limits
 *  answer 507, and without the code the UI sends people to free space in the
 *  wrong place). An error body is a few hundred bytes from a server that has
 *  already answered, so a second is orders of magnitude past normal and still
 *  turns "never settles" into "settles". */
const CODE_READ_BUDGET_MS = 1000;

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
 *
 * BOUNDED, and the timer belongs HERE rather than on `errorCode` — this is the
 * only place the body is read, and the callers that matter most (the item-level
 * send, `execShell`) reach it directly. A bound placed on the wrapper would look
 * right and do nothing for them.
 *
 * Every caller uses this to decide how to REJECT, so waiting here is waiting to
 * fail — and a body that is delivered but never closed (an ingress cutting a
 * stream mid-flight) would otherwise turn a rejection into a promise that never
 * settles. Downstream that is not a slow error, it is a missing one: the chat's
 * reconnect loop never re-enters, the composer never unlocks, the write-failure
 * notice never fires.
 *
 * The deadline RESOLVES the read rather than racing it away — the clone is left
 * to finish or die on its own, its failure already swallowed.
 */
export async function errorInfo(
  resp: Response,
): Promise<{ code?: string; also?: string[]; detail?: Record<string, unknown>; text?: string }> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    // Text, not `json()`: a caller that wants the raw body for its message must
    // get it from HERE. Reading it again off the original response is a second
    // unbounded await, and a deadline on the first read buys nothing when the
    // next line waits forever on the same stalled stream — which is exactly how
    // `execShell` kept locking the terminal after this was first bounded.
    const read = resp
      .clone()
      .text()
      .catch(() => undefined); // no body, or a stream that errored
    const raw = await Promise.race([
      read,
      new Promise<undefined>((resolve) => {
        timer = setTimeout(() => resolve(undefined), CODE_READ_BUDGET_MS);
      }),
    ]);
    if (raw === undefined) return {};
    let body: { detail?: { error?: unknown; also?: unknown } } | undefined;
    try {
      body = JSON.parse(raw);
    } catch {
      return { text: raw }; // not JSON — the status and the words are all we have
    }
    const detail = body?.detail;
    const code = typeof detail?.error === "string" ? detail.error : undefined;
    const also = Array.isArray(detail?.also)
      ? detail.also.filter((c: unknown): c is string => typeof c === "string")
      : undefined;
    // The whole object, not just the code: a quota refusal carries the numbers
    // behind it, and a message that names a limit without one cannot be checked
    // by the person reading it.
    return {
      code,
      also,
      detail: detail && typeof detail === "object" ? (detail as Record<string, unknown>) : undefined,
      text: raw,
    };
  } catch {
    return {}; // `clone()` refuses an already-consumed body, synchronously
  } finally {
    clearTimeout(timer);
  }
}
