// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from "vitest";

import {
  getStored,
  PRESETS,
  setStored,
  toBodyEnhancements,
  withWikiFlag,
  type EnhancementSelection,
} from "./kbEnhancementMode";

describe("kbEnhancementMode — translation table", () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("quick → all-zero enhancement payload", () => {
    expect(toBodyEnhancements({ mode: "quick" })).toEqual({
      expand: 0,
      hyde: 0,
      rerank: false,
    });
  });

  it("standard → undefined so BE inherits operator defaults", () => {
    expect(toBodyEnhancements({ mode: "standard" })).toBeUndefined();
  });

  it("thorough → large numbers so BE clamps to operator max", () => {
    const body = toBodyEnhancements({ mode: "thorough" });
    expect(body?.rerank).toBe(true);
    expect((body?.expand ?? 0) >= 10).toBe(true);
    expect((body?.hyde ?? 0) >= 10).toBe(true);
  });

  it("custom → forwards the user's exact slider values", () => {
    const sel: EnhancementSelection = {
      mode: "custom",
      custom: { expand: 2, hyde: 1, rerank: false },
    };
    expect(toBodyEnhancements(sel)).toEqual({ expand: 2, hyde: 1, rerank: false });
  });
});

describe("kbEnhancementMode — sticky storage", () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("defaults to standard when nothing is stored", () => {
    expect(getStored()).toEqual({ mode: "standard" });
  });

  it("round-trips a mode + custom values through localStorage", () => {
    const sel: EnhancementSelection = {
      mode: "custom",
      custom: { expand: 3, hyde: 1, rerank: true },
    };
    setStored(sel);
    expect(getStored()).toEqual(sel);
  });

  it("clamps negative / fractional custom values on read", () => {
    setStored({
      mode: "custom",
      custom: { expand: -5, hyde: 1.7, rerank: true },
    });
    const back = getStored();
    expect(back.custom).toEqual({ expand: 0, hyde: 1, rerank: true });
  });

  it("ignores garbage JSON, falls back to standard", () => {
    localStorage.setItem("rca.kbEnhancementMode", "{not-json");
    expect(getStored()).toEqual({ mode: "standard" });
  });

  it("ignores unknown mode strings", () => {
    localStorage.setItem(
      "rca.kbEnhancementMode",
      JSON.stringify({ mode: "lightning" }),
    );
    expect(getStored()).toEqual({ mode: "standard" });
  });
});

describe("withWikiFlag — folds the wiki toggle into the depth body", () => {
  it("adds wiki:true on top of an explicit depth body", () => {
    expect(withWikiFlag({ expand: 1, hyde: 0, rerank: true }, true)).toEqual({
      expand: 1,
      hyde: 0,
      rerank: true,
      wiki: true,
    });
  });

  it("produces { wiki: true } even when the depth body is undefined (standard)", () => {
    // standard depth sends no payload; wiki-on alone must still reach the BE.
    expect(withWikiFlag(undefined, true)).toEqual({ wiki: true });
  });

  it("leaves the body untouched when the wiki toggle is off", () => {
    expect(withWikiFlag({ expand: 2, hyde: 0, rerank: false }, false)).toEqual({
      expand: 2,
      hyde: 0,
      rerank: false,
    });
    expect(withWikiFlag(undefined, false)).toBeUndefined();
  });
});

describe("kbEnhancementMode — preset table sanity", () => {
  it("quick presets disable every enhancement", () => {
    expect(PRESETS.quick).toEqual({ expand: 0, hyde: 0, rerank: false });
  });
  it("standard preset matches bundled BE defaults (expand=1, hyde=0, rerank=on)", () => {
    expect(PRESETS.standard).toEqual({ expand: 1, hyde: 0, rerank: true });
  });
  it("thorough preset asks for everything", () => {
    expect(PRESETS.thorough.rerank).toBe(true);
    expect(PRESETS.thorough.expand >= 10).toBe(true);
    expect(PRESETS.thorough.hyde >= 1).toBe(true);
  });
});
