import { describe, expect, it } from "vitest";

import type { EntityFieldSpec } from "../../api/entities";
import { selectColor } from "./selectColor";

const status = (colors?: Record<string, string>): EntityFieldSpec => ({ name: "status", role: "status", colors });

describe("selectColor (#GH-projects B)", () => {
  it("is stable — the same value always gets the same hue", () => {
    expect(selectColor("in_progress")).toEqual(selectColor("in_progress"));
  });

  it("uses a coloured (non-neutral) slot for a set value", () => {
    // hashed values land in slots 1..6, never the neutral 7.
    expect(selectColor("open").fg).not.toBe("var(--cat-7-fg)");
  });

  it("returns the neutral slot for an empty value", () => {
    expect(selectColor("")).toEqual({ bg: "var(--cat-7-bg)", fg: "var(--cat-7-fg)" });
  });

  it("honours a schema colours override by hue name", () => {
    expect(selectColor("open", status({ open: "green" })).fg).toBe("var(--cat-1-fg)");
    expect(selectColor("done", status({ done: "violet" })).fg).toBe("var(--cat-4-fg)");
  });

  it("honours a numeric / cat-N override and falls back to neutral for junk", () => {
    expect(selectColor("x", status({ x: "5" })).fg).toBe("var(--cat-5-fg)");
    expect(selectColor("y", status({ y: "cat-2" })).fg).toBe("var(--cat-2-fg)");
    expect(selectColor("z", status({ z: "chartreuse" })).fg).toBe("var(--cat-7-fg)");
  });
});
