/**
 * `keyColumn` — where a key's marking strings live in a layer. The ONE lookup:
 * selectionValues (what a marking holds) and a marking's own lighting (#856)
 * must read the same column, or a key a channel sends as time / numbers is
 * written as "2024-01-02" and compared as "1704153600000" (review round 2).
 */
import { describe, expect, it } from "vitest";

import { keyColumn } from "./selection";
import { cat, f64, layer, time } from "./testAnswer";
import { canon } from "./wire";

describe("keyColumn", () => {
  it("prefers the $key.<name> marking strings over the channel's encoding", () => {
    const ly = layer("line", 2, {
      day: time(["2024-01-01", "2024-01-02"]),
      "$key.day": cat(["2024-01-01", "2024-01-02"]),
    });
    const col = keyColumn(ly, "day");
    expect(col && canon(col.value(1))).toBe("2024-01-02");
  });

  it("reads a key sent as a category directly", () => {
    const col = keyColumn(layer("bar", 1, { lot: cat(["A"]), v: f64([1]) }), "lot");
    expect(col?.value(0)).toBe("A");
  });

  it("has nothing for a key the layer does not carry", () => {
    expect(keyColumn(layer("bar", 1, { v: f64([1]) }), "lot")).toBeNull();
  });
});
