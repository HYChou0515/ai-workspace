/**
 * The one place a failed WRITE is recorded, so that no save can fail in silence.
 *
 * Why a module-level store rather than React state: the QueryClient is built at
 * module scope (`api/queryClient.ts`) and its `MutationCache.onError` fires
 * outside any component, so it has no `setState` to call. The notice subscribes
 * with `useSyncExternalStore` instead — React reads this, this never reads React.
 *
 * It holds ONE failure, the latest. A queue would let a flaky connection stack up
 * a column of identical banners, and the honest message in that situation is
 * still just "the last thing you did didn't save".
 *
 * The COPY is not decided here. A store that formatted its own message would
 * have to know the current locale, which is a React concern — so this carries
 * the two facts the component needs (the HTTP status, when there was one, and
 * the raw message) and lets the notice choose the words.
 */

export type WriteFailure = {
  /** Distinguishes two consecutive failures that read identically, so the notice
   *  re-announces rather than looking like the stale one never cleared. */
  id: number;
  /** The HTTP status, or null when the request never got one (network drop). */
  status: number | null;
  /** The server's machine-readable reason (`{"detail": {"error": …}}`), when it
   *  sent one — 507 means three different things and they need three remedies. */
  code?: string;
  /** Developer-facing detail, shown as the second line. Never the whole story:
   *  the notice's own copy says what it means for the user. */
  message: string;
};

let current: WriteFailure | null = null;
let seq = 0;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

/** Record a failed write. Anything can be thrown, so anything is accepted. */
export function reportWriteFailure(error: unknown): void {
  const status =
    typeof error === "object" && error !== null && typeof (error as { status?: unknown }).status === "number"
      ? (error as { status: number }).status
      : null;
  const code =
    typeof error === "object" && error !== null && typeof (error as { code?: unknown }).code === "string"
      ? (error as { code: string }).code
      : undefined;
  const message = error instanceof Error ? error.message : String(error);
  seq += 1;
  current = { id: seq, status, code, message };
  emit();
}

export function dismissWriteFailure(): void {
  if (current === null) return;
  current = null;
  emit();
}

export function currentWriteFailure(): WriteFailure | null {
  return current;
}

export function subscribeWriteFailures(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Tests only — the store outlives a single `render`, being module scope. */
export function resetWriteFailures(): void {
  current = null;
  seq = 0;
}
