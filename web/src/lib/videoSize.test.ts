import { describe, expect, it } from "vitest";

import {
  autoUiScale,
  estimateMegabytes,
  frameFor,
  RESOLUTION_STEPS,
  resolveSize,
  TEXT_SIZES,
} from "./videoSize";

describe("autoUiScale — the player's own rule (`player.ui_scale`), mirrored", () => {
  it("is the frame relative to 1280×720, floored at 1", () => {
    // The three examples `VideoOptions.scale`'s docstring gives.
    expect(autoUiScale(1280, 720)).toBe(1);
    expect(autoUiScale(1920, 1080)).toBe(1.5);
    expect(autoUiScale(3840, 2160)).toBe(3);
    // A square or small frame keeps the base size rather than shrinking it.
    expect(autoUiScale(720, 720)).toBe(1);
    expect(autoUiScale(640, 360)).toBe(1);
  });
});

describe("frameFor — an aspect at a short side", () => {
  it("names the frames people know by their short side", () => {
    expect(frameFor("16:9", 720)).toEqual({ width: 1280, height: 720 });
    expect(frameFor("16:9", 1080)).toEqual({ width: 1920, height: 1080 });
    expect(frameFor("4:3", 720)).toEqual({ width: 960, height: 720 });
    expect(frameFor("1:1", 720)).toEqual({ width: 720, height: 720 });
    // Portrait: the short side is the width.
    expect(frameFor("9:16", 720)).toEqual({ width: 720, height: 1280 });
  });

  it("is always even on both sides — the encoder's rule (yuv420p)", () => {
    for (const p of RESOLUTION_STEPS) {
      for (const aspect of ["16:9", "4:3", "1:1", "9:16"] as const) {
        const { width, height } = frameFor(aspect, p);
        expect(width % 2, `${aspect} ${p}`).toBe(0);
        expect(height % 2, `${aspect} ${p}`).toBe(0);
      }
    }
    expect(frameFor("16:9", 1081).height % 2).toBe(0);
  });
});

describe("resolveSize — three ways to say one W×H + text scale", () => {
  it("resolution: aspect + short side, text scale automatic", () => {
    expect(resolveSize({ mode: "resolution", aspect: "16:9", p: 1080 })).toEqual({
      width: 1920,
      height: 1080,
      scale: 1.5,
      scaleIsAuto: true,
    });
  });

  it("text: aspect + a text size, the frame follows so the text is exactly that big", () => {
    // 1.5× text on 16:9 = the 720p layout drawn at 1.5× = 1080p, scale pinned
    // (not automatic: automatic would give 4:3 at 1440×1080 only 1.125×).
    expect(resolveSize({ mode: "text", aspect: "16:9", textScale: 1.5 })).toEqual({
      width: 1920,
      height: 1080,
      scale: 1.5,
      scaleIsAuto: false,
    });
    expect(resolveSize({ mode: "text", aspect: "4:3", textScale: 1.5 })).toEqual({
      width: 1440,
      height: 1080,
      scale: 1.5,
      scaleIsAuto: false,
    });
    expect(TEXT_SIZES).toContain(1.5);
  });

  it("custom: width × height as typed, rounded to even, text scale automatic", () => {
    expect(resolveSize({ mode: "custom", width: 1001, height: 601 })).toEqual({
      width: 1000,
      height: 600,
      scale: 1,
      scaleIsAuto: true,
    });
  });
});

describe("estimateMegabytes — from the two measured points, for the result line", () => {
  it("reproduces the measurements it was derived from (41 s, docs/chat-video.md)", () => {
    expect(estimateMegabytes("mp4", 1280 * 720, 41)).toBeCloseTo(1.8, 0);
    expect(estimateMegabytes("gif", 1280 * 720, 41)).toBeCloseTo(18, 0);
    expect(estimateMegabytes("mp4", 1920 * 1080, 41)).toBeCloseTo(2.5, 0);
    expect(estimateMegabytes("gif", 1920 * 1080, 41)).toBeCloseTo(36, 0);
  });

  it("grows with length and with pixels, and gif is the big one", () => {
    expect(estimateMegabytes("mp4", 1280 * 720, 82)).toBeGreaterThan(
      estimateMegabytes("mp4", 1280 * 720, 41),
    );
    expect(estimateMegabytes("gif", 1280 * 720, 41)).toBeGreaterThan(
      estimateMegabytes("mp4", 1280 * 720, 41),
    );
  });
});
