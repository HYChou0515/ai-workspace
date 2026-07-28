import { Marp } from "@marp-team/marp-core";

/**
 * Render Marp markdown to a self-contained `{ html, css }` via the official
 * engine. Two options matter for embedding in a sanitized shadow root:
 *  - `script: false` — no marp browser runtime script (it can't run past
 *    DOMPurify anyway).
 *  - `inlineSVG: false` — emit plain `<section>` slides instead of wrapping each
 *    in `<svg><foreignObject>`; DOMPurify strips foreignObject (an XSS vector)
 *    and would drop slides with it, and we do our own CSS fit-to-width scaling
 *    so the SVG wrapper buys us nothing.
 * The cost of both being off is only that fit-headers don't auto-shrink;
 * themes, layout, pagination, math and images render faithfully. This is the
 * ONE module that imports marp-core.
 */
export function renderMarp(text: string): { html: string; css: string } {
  const { html, css } = new Marp({ script: false, inlineSVG: false }).render(text);
  return { html, css };
}
