import { useCallback, useRef, useState } from "react";

import { BREAKPOINTS } from "../lib/breakpoints";

/**
 * Measure the width a component actually HAS, rather than the width of the
 * window (#fe-responsive).
 *
 * `useIsNarrow()` asks the viewport, which is right for a shell that fills it
 * and wrong for one that doesn't. The workspace shell is the second kind: a
 * chat-first App puts a 240px chat rail in front of it, so at a 768px viewport
 * the shell has 528px — and laying out activity bar + file tree + editor +
 * chat for "wide" into 528px is exactly how the columns ended up clipped.
 *
 * Returns a ref callback to attach to the element, plus its current width in
 * px. The width is `0` until the element is attached and the observer fires
 * (and stays `0` where `ResizeObserver` is unavailable), which callers read as
 * "not measured — fall back to the viewport" via `shellIsNarrow`.
 */
export function useContainerWidth<T extends HTMLElement>(): [
  (node: T | null) => void,
  number,
] {
  const [width, setWidth] = useState(0);
  const observer = useRef<ResizeObserver | null>(null);

  const ref = useCallback((node: T | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!node || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setWidth(entry.contentRect.width);
    });
    ro.observe(node);
    observer.current = ro;
  }, []);

  return [ref, width];
}

/**
 * Whether a shell of `containerWidth` px should use its narrow, single-column
 * layout. `containerWidth` of 0/null means "not measured yet" — before the
 * first observation there is nothing better than the viewport's own verdict,
 * and starting from the viewport keeps the first paint stable.
 *
 * The threshold is `BREAKPOINTS.shell`, the width this shell's four columns
 * actually need — NOT the app-wide `narrow`, which sizes two-column grids
 * elsewhere and left a 768-870px band where the shell claimed to be wide with
 * no room to prove it.
 */
export function shellIsNarrow(
  containerWidth: number | null,
  viewportNarrow: boolean,
): boolean {
  if (!containerWidth) return viewportNarrow;
  return containerWidth < BREAKPOINTS.shell;
}
