/**
 * Row windowing for the sheet (docs/plan-ai-sheet.md Phase 3) — pure, so the
 * arithmetic is testable even though the measurement it consumes only exists in
 * a real browser.
 */

/** Rows rendered when the viewport height isn't known yet. A viewport of 0 is
 * the normal state before layout (and the permanent state in happy-dom); taking
 * it literally would render NO rows, so the grid would look empty on first paint
 * and in every component test. */
const UNMEASURED_ROWS = 30;

export function visibleRange({
  scrollTop,
  viewportHeight,
  rowHeight,
  total,
  overscan = 5,
}: {
  scrollTop: number;
  viewportHeight: number;
  rowHeight: number;
  total: number;
  overscan?: number;
}): { start: number; end: number } {
  const onScreen = viewportHeight > 0 ? Math.ceil(viewportHeight / rowHeight) : UNMEASURED_ROWS;
  const first = Math.floor(scrollTop / rowHeight);
  const start = Math.max(0, Math.min(first - overscan, Math.max(0, total)));
  const end = Math.min(total, first + onScreen + overscan);
  return { start, end: Math.max(start, end) };
}
