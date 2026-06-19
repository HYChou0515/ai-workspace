/**
 * Pure pan/zoom math for the image viewer (no DOM) — the reducer behind
 * ImageRenderer's wheel-to-zoom + drag-to-pan. Keeping it pure makes the tricky
 * parts (cursor-anchored zoom, edge clamping) deterministically unit-testable;
 * the component only wires DOM events to these functions and applies the result
 * as a CSS transform.
 *
 * Coordinate model: the image is drawn at its fitted `base` size (CSS px), with
 * `transform: translate(tx, ty) scale(scale)` and `transform-origin: 0 0`. So
 * the on-screen image rect is [tx, tx + base.w*scale] × [ty, ty + base.h*scale],
 * in viewport-local coordinates (0,0 = the viewport's top-left).
 */

export type Size = { w: number; h: number };
export type PanZoom = { scale: number; tx: number; ty: number };

// Mirror VSCode's image preview zoom span — generous in both directions.
export const MIN_SCALE = 0.1;
export const MAX_SCALE = 20;

/** The image's size at scale 1: contained within the viewport (aspect kept),
 * but never upscaled beyond its natural size (a small image stays crisp). */
export function fitSize(natural: Size, viewport: Size): Size {
  if (natural.w <= 0 || natural.h <= 0) return { w: 0, h: 0 };
  const k = Math.min(1, viewport.w / natural.w, viewport.h / natural.h);
  return { w: natural.w * k, h: natural.h * k };
}

/** Clamp one axis: center the image when it's smaller than the viewport,
 * otherwise keep it covering (no gap between an edge and the viewport). */
function clampAxis(t: number, scaledLen: number, viewLen: number): number {
  if (scaledLen <= viewLen) return (viewLen - scaledLen) / 2;
  return Math.min(0, Math.max(viewLen - scaledLen, t));
}

/** Re-seat the offsets for the current scale (center-if-smaller, cover-if-larger). */
export function clampState(s: PanZoom, base: Size, viewport: Size): PanZoom {
  return {
    scale: s.scale,
    tx: clampAxis(s.tx, base.w * s.scale, viewport.w),
    ty: clampAxis(s.ty, base.h * s.scale, viewport.h),
  };
}

/** Scale 1, centered in the viewport. */
export function initialState(base: Size, viewport: Size): PanZoom {
  return clampState({ scale: 1, tx: 0, ty: 0 }, base, viewport);
}

function clampScale(scale: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
}

/** Multiply the scale by `factor` while keeping the image point under the
 * cursor (`cx`,`cy`, viewport-local) fixed; the offset is then re-clamped. */
export function zoomAt(
  s: PanZoom,
  factor: number,
  cx: number,
  cy: number,
  base: Size,
  viewport: Size,
): PanZoom {
  const scale = clampScale(s.scale * factor);
  const k = scale / s.scale; // achieved ratio (honours the clamp at the bounds)
  return clampState(
    { scale, tx: cx - (cx - s.tx) * k, ty: cy - (cy - s.ty) * k },
    base,
    viewport,
  );
}

/** Translate by a drag delta (clamped to keep the image covering). */
export function panBy(s: PanZoom, dx: number, dy: number, base: Size, viewport: Size): PanZoom {
  return clampState({ scale: s.scale, tx: s.tx + dx, ty: s.ty + dy }, base, viewport);
}

/** Whether the image overflows the viewport on either axis — i.e. there's
 * something to pan to (drives the grab cursor + whether dragging does anything). */
export function canPan(s: PanZoom, base: Size, viewport: Size): boolean {
  return base.w * s.scale > viewport.w + 0.5 || base.h * s.scale > viewport.h + 0.5;
}
