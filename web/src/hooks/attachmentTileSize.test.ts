// @vitest-environment happy-dom
/**
 * #730: the attachment grid's size control.
 *
 * The slider value is the tile's WIDTH, so these guard the two things a size
 * control can get wrong on its own: a preset that is not a size the slider can
 * reach, and a stored value from another version that puts the grid off screen.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  clampTileSize,
  readTileSize,
  TILE_DEFAULT,
  TILE_MAX,
  TILE_MIN,
  TILE_PRESETS,
  TILE_STEP,
} from "./attachmentTileSize";

describe("attachment tile size (#730)", () => {
  beforeEach(() => localStorage.clear());

  it("every preset is a size the slider itself can land on", () => {
    // A preset outside the range, or off the step grid, would light up its
    // button and then be unreachable by dragging — two controls disagreeing
    // about the same number.
    for (const preset of TILE_PRESETS) {
      expect(preset.px).toBeGreaterThanOrEqual(TILE_MIN);
      expect(preset.px).toBeLessThanOrEqual(TILE_MAX);
      expect((preset.px - TILE_MIN) % TILE_STEP).toBe(0);
    }
  });

  it("offers the default as one of them", () => {
    // Otherwise "put it back how it was" is a hunt along the slider.
    expect(TILE_PRESETS.map((p) => p.px)).toContain(TILE_DEFAULT);
  });

  it("clamps a stored value from outside the range", () => {
    // The range can change between versions; a remembered 900px would render
    // one tile per screen and look like the grid had broken.
    expect(clampTileSize(9000)).toBe(TILE_MAX);
    expect(clampTileSize(1)).toBe(TILE_MIN);
    expect(clampTileSize(Number.NaN)).toBe(TILE_DEFAULT);
  });

  it("falls back to the default when nothing is stored or it is junk", () => {
    expect(readTileSize()).toBe(TILE_DEFAULT);
    localStorage.setItem("ui:kb-attachment-tile", "banana");
    expect(readTileSize()).toBe(TILE_DEFAULT);
  });
});
