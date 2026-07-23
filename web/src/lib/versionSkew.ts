/** Version-skew forced refresh (the v2026.07.23 incident).
 *
 * The backend stamps `X-App-Version` on every response; this bundle bakes its
 * own build version (`__APP_VERSION__`, read from pyproject.toml at build — the
 * same source the backend serves). A mismatch means THIS TAB runs a stale
 * cached bundle against a newer api — the exact condition that broke every
 * chat when #601 changed the event shape. The cure is a reload: index.html is
 * served no-cache, so reloading always picks up the new hashed bundle.
 *
 * Reload policy — at a SAFE moment, exactly once:
 *   - nothing streaming → reload now;
 *   - a turn is live (any SSE open, tracked by `parseSseStream`) → wait for the
 *     LAST stream to close, then reload — never cut an answer mid-sentence.
 *
 * Absence of the header is NOT skew (a proxy or an older api may strip it);
 * only a present-and-different version triggers.
 */

const expected: string = typeof __APP_VERSION__ !== "undefined" ? __APP_VERSION__ : "";

let reloadImpl: () => void = () => window.location.reload();
let openStreams = 0;
let pending = false;
let fired = false;

/** Compare a response's `X-App-Version` against this bundle's build version and
 * schedule the one-shot reload on mismatch. Called from the `apiFetch`
 * chokepoint, so whichever call happens first after a deploy detects it. */
export function checkVersionHeader(resp: Response): void {
  if (fired || !expected) return;
  // Passive by contract: tests (and odd transports) hand fetch fakes without a
  // Headers object — a checker that throws would fail the call it rides on.
  const got = resp?.headers?.get?.("X-App-Version");
  if (!got || got === expected) return;
  if (openStreams > 0) {
    pending = true;
    return;
  }
  fired = true;
  reloadImpl();
}

/** A live SSE stream opened — hold any skew reload until it (and its siblings)
 * finish, so a streaming answer is never cut. */
export function noteStreamStart(): void {
  openStreams += 1;
}

export function noteStreamEnd(): void {
  openStreams = Math.max(0, openStreams - 1);
  if (pending && openStreams === 0 && !fired) {
    fired = true;
    reloadImpl();
  }
}

/** Test seams — production never calls these. */
export function _setReloadImpl(fn: () => void): void {
  reloadImpl = fn;
}

export function _resetForTest(): void {
  reloadImpl = () => window.location.reload();
  openStreams = 0;
  pending = false;
  fired = false;
}
