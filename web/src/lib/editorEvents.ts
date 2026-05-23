/**
 * Lightweight event bus for editor-level signals that don't have a
 * natural prop path. Used for tab-strip "Run all" → active notebook.
 * Window-level — there's one editor at a time per page.
 */

const RUN_ALL = "rca:editor:run-all";

export function emitRunAll(path: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(RUN_ALL, { detail: { path } }));
}

export function onRunAll(
  matchPath: string,
  handler: () => void,
): () => void {
  if (typeof window === "undefined") return () => undefined;
  const fn = (e: Event) => {
    const detail = (e as CustomEvent<{ path: string }>).detail;
    if (detail?.path === matchPath) handler();
  };
  window.addEventListener(RUN_ALL, fn);
  return () => window.removeEventListener(RUN_ALL, fn);
}
