/**
 * Shared responsive breakpoints (px). #464 — the app had zero width-based
 * media queries; these are the single source of truth for "narrow" (phones /
 * very slim panels) vs "wide".
 *
 * A CSS custom property can't live inside a `@media (max-width: …)` condition,
 * so the CSS files (`styles/*.css`) repeat the SAME literals in their `@media`
 * rules. If you change a value here, grep the styles for the old px and update
 * both — `breakpoints.test.ts` guards that the JS query strings stay in sync.
 */
export const BREAKPOINTS = {
  /** Below this, the three shells drop their side panels to drawers / stacks. */
  narrow: 768,
  /** Above `narrow`, below this = tablet-ish (single side panel is fine). */
  wide: 1024,
} as const;

/** Matches phones / slim viewports (< 768px). Mirrors the CSS `@media
 * (max-width: 767px)` blocks that collapse the KB grids. */
export const NARROW_QUERY = `(max-width: ${BREAKPOINTS.narrow - 1}px)`;
