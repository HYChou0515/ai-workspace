// @vitest-environment happy-dom
/** #334: sticky per-message "max KB searches" pick. */
import { beforeEach, describe, expect, it } from "vitest";

import {
  KB_SEARCH_MAX_DEFAULT,
  KB_SEARCH_MAX_UI_MAX,
  clampKbSearchMax,
  getKbSearchMax,
  setKbSearchMax,
} from "./kbSearchMax";

describe("kbSearchMax", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to KB_SEARCH_MAX_DEFAULT when nothing is stored", () => {
    expect(getKbSearchMax()).toBe(KB_SEARCH_MAX_DEFAULT);
  });

  it("round-trips a stored pick", () => {
    setKbSearchMax(5);
    expect(getKbSearchMax()).toBe(5);
  });

  it("allows 0 (don't search this reply)", () => {
    setKbSearchMax(0);
    expect(getKbSearchMax()).toBe(0);
  });

  it("clamps to [0, KB_SEARCH_MAX_UI_MAX]", () => {
    expect(clampKbSearchMax(-3)).toBe(0);
    expect(clampKbSearchMax(999)).toBe(KB_SEARCH_MAX_UI_MAX);
    expect(clampKbSearchMax(4)).toBe(4);
  });

  it("floors fractional values", () => {
    expect(clampKbSearchMax(3.9)).toBe(3);
  });

  it("setKbSearchMax clamps before persisting", () => {
    setKbSearchMax(99);
    expect(getKbSearchMax()).toBe(KB_SEARCH_MAX_UI_MAX);
  });

  it("falls back to the default on a corrupt stored value", () => {
    localStorage.setItem("rca.kbSearchMax", "not-a-number");
    expect(getKbSearchMax()).toBe(KB_SEARCH_MAX_DEFAULT);
  });
});
