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
