// @vitest-environment happy-dom
/** #506: sticky per-message "max wiki searches" pick (replaces the wiki toggle). */
import { beforeEach, describe, expect, it } from "vitest";

import {
  KB_WIKI_MAX_DEFAULT,
  KB_WIKI_MAX_UI_MAX,
  clampKbWikiMax,
  getKbWikiMax,
  setKbWikiMax,
} from "./kbWikiMax";

describe("kbWikiMax", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to KB_WIKI_MAX_DEFAULT when nothing is stored", () => {
    expect(getKbWikiMax()).toBe(KB_WIKI_MAX_DEFAULT);
  });

  it("round-trips a stored pick", () => {
    setKbWikiMax(5);
    expect(getKbWikiMax()).toBe(5);
  });

  it("allows 0 (don't grep the wiki this reply)", () => {
    setKbWikiMax(0);
    expect(getKbWikiMax()).toBe(0);
  });

  it("clamps to [0, KB_WIKI_MAX_UI_MAX]", () => {
    expect(clampKbWikiMax(-3)).toBe(0);
    expect(clampKbWikiMax(999)).toBe(KB_WIKI_MAX_UI_MAX);
    expect(clampKbWikiMax(4)).toBe(4);
  });

  it("floors fractional values", () => {
    expect(clampKbWikiMax(3.9)).toBe(3);
  });

  it("setKbWikiMax clamps before persisting", () => {
    setKbWikiMax(99);
    expect(getKbWikiMax()).toBe(KB_WIKI_MAX_UI_MAX);
  });

  it("uses its OWN storage key, independent of the kb-search pick", () => {
    // a distinct localStorage key so tuning wiki doesn't move kb_search and vice versa
    setKbWikiMax(7);
    expect(localStorage.getItem("rca.kbWikiMax")).toBe("7");
    expect(localStorage.getItem("rca.kbSearchMax")).toBeNull();
  });

  it("falls back to the default on a corrupt stored value", () => {
    localStorage.setItem("rca.kbWikiMax", "not-a-number");
    expect(getKbWikiMax()).toBe(KB_WIKI_MAX_DEFAULT);
  });
});
