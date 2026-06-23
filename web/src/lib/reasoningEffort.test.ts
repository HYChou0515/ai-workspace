// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from "vitest";

import { getReasoningEffort, setReasoningEffort } from "./reasoningEffort";

// #160: the "Auto" (don't-send) option is gone — the dial is always one of
// low/medium/high and defaults to the lightest.
describe("reasoning effort sticky store", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to low when unset", () => {
    expect(getReasoningEffort()).toBe("low");
  });

  it("round-trips every level", () => {
    setReasoningEffort("high");
    expect(getReasoningEffort()).toBe("high");
    setReasoningEffort("medium");
    expect(getReasoningEffort()).toBe("medium");
    setReasoningEffort("low");
    expect(getReasoningEffort()).toBe("low");
  });

  it("ignores a junk stored value, falling back to low", () => {
    localStorage.setItem("rca.reasoningEffort", "auto");
    expect(getReasoningEffort()).toBe("low");
  });
});
