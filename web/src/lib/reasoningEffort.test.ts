// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from "vitest";

import { getReasoningEffort, setReasoningEffort } from "./reasoningEffort";

describe("reasoning effort sticky store", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to null (model default) when unset", () => {
    expect(getReasoningEffort()).toBeNull();
  });

  it("round-trips a valid value", () => {
    setReasoningEffort("high");
    expect(getReasoningEffort()).toBe("high");
  });

  it("clears back to default with null", () => {
    setReasoningEffort("low");
    setReasoningEffort(null);
    expect(getReasoningEffort()).toBeNull();
  });

  it("ignores a junk stored value", () => {
    localStorage.setItem("rca.reasoningEffort", "bogus");
    expect(getReasoningEffort()).toBeNull();
  });
});
