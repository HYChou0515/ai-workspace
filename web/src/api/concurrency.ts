/**
 * A gate for calls that are made one at a time rather than as one batch: at
 * most `limit` run at once, the rest wait their turn in call order.
 *
 * Used by the skill install dialog, whose rows each ask their own question
 * (plan-skill-install-picker D3): unbounded, a list of many workspaces opens
 * that many requests, and a few slow sandboxes then hold every connection the
 * browser allows the host — the Install the user presses included. A waiting
 * call whose `signal` aborts (the dialog closed) leaves the queue without
 * ever starting.
 */
export function createLimiter(limit: number) {
  let active = 0;
  const waiting: Array<() => void> = [];
  // A finished call hands its slot straight to the next one waiting rather
  // than freeing it: freed, a call arriving before the waiter resumes would
  // take it too, and one more than `limit` would run.
  const release = () => {
    const next = waiting.shift();
    if (next) next();
    else active--;
  };
  return async function run<R>(fn: () => Promise<R>, signal?: AbortSignal): Promise<R> {
    if (active < Math.max(1, limit)) {
      active++;
    } else {
      await new Promise<void>((resolve, reject) => {
        const go = () => {
          signal?.removeEventListener("abort", drop);
          resolve();
        };
        const drop = () => {
          const at = waiting.indexOf(go);
          if (at >= 0) waiting.splice(at, 1);
          reject(signal?.reason ?? new DOMException("aborted", "AbortError"));
        };
        if (signal?.aborted) return drop();
        signal?.addEventListener("abort", drop, { once: true });
        waiting.push(go);
      });
    }
    try {
      return await fn();
    } finally {
      release();
    }
  };
}

/**
 * Run an async map with a bounded number of tasks in flight at once.
 *
 * Used by uploads: a folder pick can yield hundreds/thousands of files, and
 * firing them all through one `Promise.all` fans out that many simultaneous
 * requests — enough to freeze the tab and never flush anything to the server.
 * A small pool keeps the UI responsive while still uploading everything.
 *
 * Results are returned in input order regardless of completion order.
 */
export async function mapWithConcurrency<T, R>(
  items: readonly T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;
  const workers = Math.min(Math.max(1, limit), items.length);
  await Promise.all(
    Array.from({ length: workers }, async () => {
      // `next++` is atomic on JS's single thread — each worker claims the next
      // index, so no item runs twice and none is skipped.
      while (next < items.length) {
        const i = next++;
        results[i] = await fn(items[i], i);
      }
    }),
  );
  return results;
}
