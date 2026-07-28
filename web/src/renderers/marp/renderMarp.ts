import { Marp } from "@marp-team/marp-core";

/**
 * Render Marp markdown to a self-contained `{ html, css }` via the official
 * engine. `script: false` keeps the output free of marp's browser runtime
 * script — it can't run inside the sanitized shadow root anyway, so fit-headers
 * won't JS-shrink, but everything else (themes, layout, pagination, math,
 * images) renders faithfully. This is the ONE module that imports marp-core.
 */
export function renderMarp(text: string): { html: string; css: string } {
  const { html, css } = new Marp({ script: false }).render(text);
  return { html, css };
}
