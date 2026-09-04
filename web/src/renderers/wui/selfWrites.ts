/**
 * Keep a page from being told about its own save.
 *
 * Every write broadcasts `file_changed` to everyone looking at the item — the
 * writer included. Forwarded blindly, an editor hears "somebody else changed
 * this" after every one of its own saves, which is worse than not forwarding at
 * all: the one alarm that matters arrives already discredited.
 *
 * Matching is per path and per write, not "is it recent": two saves in flight
 * produce two events, and swallowing both is what keeps a busy page quiet.
 * The window only bounds the damage when an event never arrives (a failed
 * write, a dropped stream) — without it, one lost event would mute that path
 * for the life of the page.
 */

const WINDOW_MS = 15_000;

export type SelfWrites = {
  /** This page just wrote `path`; expect one echo. */
  record(path: string): void;
  /** True if this event is that echo — in which case it is consumed. */
  consume(path: string): boolean;
};

export function createSelfWrites(
  now: () => number = Date.now,
  windowMs: number = WINDOW_MS,
): SelfWrites {
  const pending = new Map<string, number[]>();

  const fresh = (times: number[]): number[] => {
    const cutoff = now() - windowMs;
    return times.filter((t) => t >= cutoff);
  };

  return {
    record(path) {
      pending.set(path, [...fresh(pending.get(path) ?? []), now()]);
    },
    consume(path) {
      const times = fresh(pending.get(path) ?? []);
      if (times.length === 0) {
        pending.delete(path);
        return false;
      }
      times.shift();
      if (times.length) pending.set(path, times);
      else pending.delete(path);
      return true;
    },
  };
}
