import { useEffect, useRef } from "react";

/** Default cadence for the store-poll fallback. Comfortably above the
 * same-pod first-event latency so a healthy live stream is never polled
 * over (the `isLive` gate skips a poll while events are arriving). */
export const STORE_POLL_MS = 2500;

/**
 * Cross-pod safety net for the per-pod live SSE broadcast (issue #202). A chat's
 * turn events live in the in-memory broadcast of whichever pod ran the turn; a
 * viewer whose `/stream` lands on a DIFFERENT pod receives nothing and the
 * composer stays stuck on "working…". The persisted thread, by contrast, is on
 * the SHARED store and readable from any pod.
 *
 * So: while a turn is in flight (`active`) and the live stream is silent
 * (`!isLive()`), poll the persisted thread (`fetchThread`) every `pollMs` and
 * hand the snapshot to `onItems`. The caller decides what the snapshot means
 * (still running vs done) and updates its log — clearing `active` ends the
 * polling. When the live stream IS delivering (same-pod), `isLive()` is true so
 * the poll is skipped and the live deltas are never clobbered.
 *
 * Callbacks are read through a ref so passing fresh closures each render does
 * NOT restart the interval — only `active` / `pollMs` do.
 */
export function useStorePollFallback<T>({
  active,
  isLive,
  fetchThread,
  onSnapshot,
  pollMs = STORE_POLL_MS,
}: {
  /** A turn is in flight — poll only while this holds (e.g. `log.streaming`). */
  active: boolean;
  /** True when a live SSE event arrived recently — skip the poll so the live
   * stream (same-pod) is authoritative and its deltas aren't overwritten. */
  isLive: () => boolean;
  /** Read the persisted thread from the shared store (any pod serves it). */
  fetchThread: () => Promise<T>;
  /** Receive each silent-poll snapshot of the persisted thread. */
  onSnapshot: (snapshot: T) => void;
  pollMs?: number;
}): void {
  const ref = useRef({ isLive, fetchThread, onSnapshot });
  ref.current = { isLive, fetchThread, onSnapshot };

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const id = setInterval(async () => {
      if (ref.current.isLive()) return;
      try {
        const snapshot = await ref.current.fetchThread();
        if (!cancelled) ref.current.onSnapshot(snapshot);
      } catch {
        // Transient store-read failure — just retry on the next tick.
      }
    }, pollMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [active, pollMs]);
}
