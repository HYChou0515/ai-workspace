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
  /**
   * The width the WORKSPACE SHELL specifically needs before its four columns
   * can coexist: activity bar 50 + file tree min 180 + editor min 360 + chat
   * min 280 = 870. `narrow` (768) sizes the KB/dashboard grids, which stack
   * two columns; borrowing it here left a 768-870px band where the shell
   * called itself wide and squeezed the editor to ~190px — markdown wrapping
   * at two glyphs a line — while the top bar's item title got 6px.
   *
   * Change a column minimum (EDITOR_MIN_W / ACTIVITY_BAR_W / the sidebar
   * clamp in WorkspaceShell) and this number should move with it.
   */
  shell: 870,
} as const;

/** Matches phones / slim viewports (< 768px). Mirrors the CSS `@media
 * (max-width: 767px)` blocks that collapse the KB grids. */
export const NARROW_QUERY = `(max-width: ${BREAKPOINTS.narrow - 1}px)`;
