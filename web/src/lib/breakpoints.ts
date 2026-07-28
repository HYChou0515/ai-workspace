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
   * can coexist, in the configuration a user actually lands in: activity bar
   * 50 + file tree 260 (the DEFAULT `rca:layout:sidebar`, not its 180 clamp
   * floor) + editor min 360 + chat min 280 = 950.
   *
   * `narrow` (768) sizes the KB/dashboard grids, which stack two columns;
   * borrowing it here left a band where the shell called itself wide and
   * squeezed the editor to ~190px — markdown wrapping at two glyphs a line —
   * while the top bar's item title got 6px.
   *
   * A user who drags the tree wider than 260 buys that width out of the
   * editor, by their own hand, and `EDITOR_MIN_W` is not enforced as a CSS
   * min-width — so this number guarantees the DEFAULT layout fits, not every
   * layout. Change a default here (EDITOR_MIN_W / ACTIVITY_BAR_W / the
   * sidebar default in WorkspaceShell) and this number moves with it.
   */
  shell: 950,
  /** Width of the chat-first rail (`chat-rail.css`), expanded. The rail sits
   * BESIDE the shell, so a chat-first App needs `shell + this` before the
   * shell's four columns fit — see `ChatListRail`, which tucks itself rather
   * than being the thing that forces the shell into its narrow layout. */
  chatRail: 240,
} as const;

/** Matches phones / slim viewports (< 768px). Mirrors the CSS `@media
 * (max-width: 767px)` blocks that collapse the KB grids. */
export const NARROW_QUERY = `(max-width: ${BREAKPOINTS.narrow - 1}px)`;
