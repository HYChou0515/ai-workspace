import { describe, expect, it } from "vitest";

import {
  MAX_SCALE,
  MIN_SCALE,
  type PanZoom,
  type Size,
  canPan,
  clampState,
  fitSize,
  initialState,
  panBy,
  zoomAt,
} from "./panZoom";

const VP: Size = { w: 400, h: 400 };
const BASE: Size = { w: 400, h: 300 }; // an 800x600 image fitted into VP

describe("fitSize", () => {
  it("shrinks an oversized image to contain it in the viewport (aspect kept)", () => {
    expect(fitSize({ w: 800, h: 600 }, VP)).toEqual({ w: 400, h: 300 });
    expect(fitSize({ w: 600, h: 1200 }, VP)).toEqual({ w: 200, h: 400 });
  });
  it("never upscales an image smaller than the viewport (shown at natural size)", () => {
    expect(fitSize({ w: 100, h: 100 }, VP)).toEqual({ w: 100, h: 100 });
  });
});

describe("initialState", () => {
  it("is scale 1, centered in the viewport", () => {
    // base 400x300 in a 400x400 viewport → flush left, 50px top/bottom letterbox
    expect(initialState(BASE, VP)).toEqual({ scale: 1, tx: 0, ty: 50 });
  });
});

describe("clampState", () => {
  it("centers each axis the scaled image is smaller than the viewport", () => {
    const s: PanZoom = { scale: 1, tx: 999, ty: -999 };
    expect(clampState(s, BASE, VP)).toEqual({ scale: 1, tx: 0, ty: 50 });
  });
  it("keeps a zoomed-in image covering the viewport (no gap at the edges)", () => {
    // scale 2 → scaled 800x600, both bigger than the 400 viewport.
    expect(clampState({ scale: 2, tx: 50, ty: 50 }, BASE, VP)).toEqual({
      scale: 2,
      tx: 0, // can't drag a covering edge inward past 0
      ty: 0,
    });
    expect(clampState({ scale: 2, tx: -999, ty: -999 }, BASE, VP)).toEqual({
      scale: 2,
      tx: -400, // vp.w - scaledW = 400 - 800
      ty: -200, // vp.h - scaledH = 400 - 600
    });
  });
});

describe("zoomAt", () => {
  it("keeps the image point under the cursor fixed while zooming", () => {
    const start = initialState(BASE, VP); // {1, 0, 50}
    const next = zoomAt(start, 2, 200, 200, BASE, VP);
    expect(next.scale).toBe(2);
    // image point under the cursor is unchanged: (cx - tx) / scale
    const before = { x: (200 - start.tx) / start.scale, y: (200 - start.ty) / start.scale };
    const after = { x: (200 - next.tx) / next.scale, y: (200 - next.ty) / next.scale };
    expect(after.x).toBeCloseTo(before.x);
    expect(after.y).toBeCloseTo(before.y);
  });
  it("clamps the scale to [MIN_SCALE, MAX_SCALE]", () => {
    const start = initialState(BASE, VP);
    expect(zoomAt(start, 1e6, 200, 200, BASE, VP).scale).toBe(MAX_SCALE);
    expect(zoomAt(start, 1e-6, 200, 200, BASE, VP).scale).toBe(MIN_SCALE);
  });
  it("re-clamps the offset so a zoom never opens a gap", () => {
    const z = zoomAt(initialState(BASE, VP), 2, 0, 0, BASE, VP);
    // zooming toward the top-left corner must not pull the image off the right/bottom
    expect(z.tx).toBeLessThanOrEqual(0);
    expect(z.tx).toBeGreaterThanOrEqual(VP.w - BASE.w * z.scale);
  });
});

describe("panBy", () => {
  it("translates by the drag delta, clamped to keep the image covering", () => {
    const zoomed: PanZoom = { scale: 2, tx: -200, ty: -100 };
    expect(panBy(zoomed, 50, 25, BASE, VP)).toEqual({ scale: 2, tx: -150, ty: -75 });
    // overshoot is clamped to the covering bound
    expect(panBy(zoomed, 9999, 9999, BASE, VP)).toEqual({ scale: 2, tx: 0, ty: 0 });
  });
});

describe("canPan", () => {
  it("is false at fit (image fully visible) and true once zoomed past the viewport", () => {
    expect(canPan(initialState(BASE, VP), BASE, VP)).toBe(false);
    expect(canPan({ scale: 2, tx: 0, ty: 0 }, BASE, VP)).toBe(true);
  });
});
