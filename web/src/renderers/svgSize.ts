/**
 * Recover an SVG's intrinsic size from its source text.
 *
 * Why not the <img>'s `naturalWidth`/`naturalHeight`? For an SVG that declares a
 * `viewBox` but no `width`/`height` — what mermaid / draw.io / Illustrator
 * commonly export — browsers report a tiny placeholder intrinsic size (Chrome
 * fits the 300×150 replaced-element default to the viewBox aspect, e.g.
 * 214×150), NOT the real viewBox dimensions. The image viewer then shows the
 * diagram as a small island it can never fill, so the user zooms in and the
 * (now bitmap-cached) `<img>` blurs (#185). Parsing the viewBox here recovers the
 * true size so a vector SVG can fill the pane crisply at scale 1.
 */
import type { Size } from "./panZoom";

/** The intrinsic size of an SVG from its source, or null when it can't be
 * determined (no viewBox and no absolute width/height — fall back to the
 * `<img>`'s reported size). `viewBox` wins: it carries the real coordinate
 * extent even when width/height are percentages or absent. */
export function svgNaturalSize(svgText: string): Size | null {
  const viewBox = /viewBox\s*=\s*["']\s*[-+0-9.]+[ ,]+[-+0-9.]+[ ,]+([0-9.]+)[ ,]+([0-9.]+)/i.exec(
    svgText,
  );
  if (viewBox) {
    const w = Number(viewBox[1]);
    const h = Number(viewBox[2]);
    if (w > 0 && h > 0) return { w, h };
  }
  const w = _absLen(svgText, "width");
  const h = _absLen(svgText, "height");
  if (w !== null && h !== null) return { w, h };
  return null;
}

/** A root `width`/`height` attribute as pixels, or null when absent or a
 * non-pixel unit (`%`, `em`, …) — those carry no intrinsic px size, so the
 * viewBox (if any) governs and otherwise we defer to the <img>. A bare number
 * or an explicit `px` suffix counts as pixels. */
function _absLen(svgText: string, attr: string): number | null {
  const m = new RegExp(`[\\s<]${attr}\\s*=\\s*["']\\s*([0-9]*\\.?[0-9]+)(px)?\\s*["']`, "i").exec(
    svgText,
  );
  if (!m) return null;
  const n = Number(m[1]);
  return n > 0 ? n : null;
}
