import { describe, expect, it } from "vitest";

import { parseFishbone } from "./schema";

describe("parseFishbone", () => {
  it("parses the canonical 6M schema", () => {
    const fb = parseFishbone(
      JSON.stringify({
        effect: "Bridging on lot 25-W14",
        branches: [
          { label: "Machine", side: "top", items: [{ t: "Zone-3 drift", strong: true }] },
          { label: "Method", side: "bot", items: [{ t: "Stencil A4" }] },
        ],
      }),
    );
    expect(fb).not.toBeNull();
    expect(fb?.effect).toBe("Bridging on lot 25-W14");
    expect(fb?.branches[0]?.items[0]?.strong).toBe(true);
    expect(fb?.branches[1]?.items[0]?.strong).toBe(false);
  });

  it("returns null on malformed JSON", () => {
    expect(parseFishbone("not json")).toBeNull();
  });

  it("returns null when effect or branches are missing", () => {
    expect(parseFishbone(JSON.stringify({ branches: [] }))).toBeNull();
    expect(parseFishbone(JSON.stringify({ effect: "x" }))).toBeNull();
  });

  it("returns null when a branch uses a non-6M label", () => {
    const out = parseFishbone(
      JSON.stringify({
        effect: "x",
        branches: [{ label: "Marketing", side: "top", items: [] }],
      }),
    );
    expect(out).toBeNull();
  });

  it("returns null when item.t is missing", () => {
    const out = parseFishbone(
      JSON.stringify({
        effect: "x",
        branches: [{ label: "Machine", side: "top", items: [{ strong: true }] }],
      }),
    );
    expect(out).toBeNull();
  });
});
