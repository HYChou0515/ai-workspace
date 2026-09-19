import { describe, expect, it } from "vitest";

import {
  allowedSteps,
  allowedTextSizes,
  autoUiScale,
  fitChoice,
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
    expect(frameFor("1:1", 720)).toEqual({ width: 720, height: 720 });
    // Portrait: the short side is the width.
    expect(frameFor("9:16", 720)).toEqual({ width: 720, height: 1280 });
  });

  it("is always even on both sides — the encoder's rule (yuv420p)", () => {
    for (const p of RESOLUTION_STEPS) {
      for (const aspect of ["16:9", "1:1", "9:16"] as const) {
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
    // The plan's four sizes (小/中/大/特大 = 0.8 / 1 / 1.3 / 1.6): 大 on 16:9 is
    // the 720p layout drawn at 1.3× — 1664×936 — with the scale PINNED, not
    // automatic (automatic would give a square frame 1 whatever the size).
    expect(TEXT_SIZES).toEqual([0.8, 1, 1.3, 1.6]);
    expect(resolveSize({ mode: "text", aspect: "16:9", textScale: 1.3 })).toEqual({
      width: 1664,
      height: 936,
      scale: 1.3,
      scaleIsAuto: false,
    });
    expect(resolveSize({ mode: "text", aspect: "1:1", textScale: 0.8 })).toEqual({
      width: 576,
      height: 576,
      scale: 0.8,
      scaleIsAuto: false,
    });
  });

  it("fits a choice to the ceiling: a stop or text size past it becomes the largest allowed; custom stays", () => {
    // A deployment capped at 500,000 px allows only 480p in 16:9 (852×480
    // = 408,960; 720p is 921,600). The dialog opens on 720p before the
    // ceiling arrives, so the choice must be fitted at render, not seeded.
    expect(fitChoice({ mode: "resolution", aspect: "16:9", p: 720 }, 500_000)).toEqual({
      mode: "resolution",
      aspect: "16:9",
      p: 480,
    });
    expect(allowedSteps("16:9", 500_000)).toEqual([480]);
    // The LARGEST allowed, not the smallest: 1,000,000 px allows 480p and
    // 720p in 16:9, and a 1080p choice lands on 720p.
    expect(allowedSteps("16:9", 1_000_000)).toEqual([480, 720]);
    expect(fitChoice({ mode: "resolution", aspect: "16:9", p: 1080 }, 1_000_000)).toEqual({
      mode: "resolution",
      aspect: "16:9",
      p: 720,
    });
    // 600,000 px in 16:9 allows only 小 (0.8 × 720p = 1024×576 = 589,824).
    expect(fitChoice({ mode: "text", aspect: "16:9", textScale: 1 }, 600_000)).toEqual({
      mode: "text",
      aspect: "16:9",
      textScale: 0.8,
    });
    expect(allowedTextSizes("16:9", 600_000)).toEqual([0.8]);
    // Inside the ceiling, or the ceiling unknown, or nothing allowed at all:
    // the choice is returned as it is (the same object).
    const ok = { mode: "resolution" as const, aspect: "16:9" as const, p: 720 };
    expect(fitChoice(ok, 1920 * 1080)).toBe(ok);
    expect(fitChoice(ok, undefined)).toBe(ok);
    expect(fitChoice(ok, 1000)).toBe(ok);
    const custom = { mode: "custom" as const, width: 4000, height: 3000 };
    expect(fitChoice(custom, 500_000)).toBe(custom);
  });

  it("custom: width × height as typed, rounded to even, text scale automatic — or pinned when asked", () => {
    expect(resolveSize({ mode: "custom", width: 1001, height: 601 })).toEqual({
      width: 1000,
      height: 600,
      scale: 1,
      scaleIsAuto: true,
    });
    expect(resolveSize({ mode: "custom", width: 1000, height: 600, textScale: 1.3 })).toEqual({
      width: 1000,
      height: 600,
      scale: 1.3,
      scaleIsAuto: false,
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
