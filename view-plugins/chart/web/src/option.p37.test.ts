/**
 * #847/#848 PR 5 P37 (review round 18): small rules on the option.
 */
import { describe, expect, it } from "vitest";

import { NAME_AT, toOption } from "./option";
import { answer, base, f64, layer } from "./testAnswer";

const scatter = { ...base, mark: "point", encoding: { x: { field: "x", type: "quantitative" }, y: { field: "y", type: "quantitative" } } };
const rows = () => answer(layer("point", 2, { x: f64([1, 2]), y: f64([3, 4]) }));

describe("row 17: the wide layout puts the y axis's name where NAME_AT does", () => {
  it("reads NAME_AT.y, the one constant axisOption reads too", () => {
    const built = toOption(scatter, rows());
    const wide = (built.layout({ compact: false }).option.yAxis as Record<string, unknown>[])[0];
    expect({ nameLocation: wide.nameLocation, nameGap: wide.nameGap }).toEqual(NAME_AT.y);
    // and a switch back from compact lands there: the drawn option says the same
    const drawn = (built.option.yAxis as Record<string, unknown>[])[0];
    expect({ nameLocation: drawn.nameLocation, nameGap: drawn.nameGap }).toEqual(NAME_AT.y);
  });
});
