// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from "vitest";

import { getKbAgentName, setKbAgentName } from "./kbAgent";

describe("kbAgent (issue #32 sticky picker)", () => {
  afterEach(() => setKbAgentName(null));

  it("defaults to null when nothing is stored", () => {
    expect(getKbAgentName()).toBeNull();
  });

  it("round-trips through localStorage so a reload preserves the pick", () => {
    setKbAgentName("KB · Claude");
    expect(getKbAgentName()).toBe("KB · Claude");
    setKbAgentName(null);
    expect(getKbAgentName()).toBeNull();
  });

  it("stores empty string as null (avoids the picker silently sending '')", () => {
    setKbAgentName("");
    expect(getKbAgentName()).toBeNull();
  });
});
