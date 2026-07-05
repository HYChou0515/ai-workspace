import { useSyncExternalStore } from "react";

import { NARROW_QUERY } from "../lib/breakpoints";

/**
 * Reactive `window.matchMedia` (#464). Unlike a one-off `window.innerWidth`
 * read, this re-renders the component when the viewport crosses the query
 * boundary, so a layout can flip between wide and narrow modes live (resize,
 * device rotation). Backed by `useSyncExternalStore` — no `useEffect` +
 * `useState` dance, tear-free.
 *
 * The server snapshot returns `false` (assume wide), so nothing collapses
 * during SSR / the first paint before hydration.
 */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** True on narrow viewports (< 768px), where the shells collapse their side
 * panels into drawers / single-column stacks. */
export function useIsNarrow(): boolean {
  return useMediaQuery(NARROW_QUERY);
}
