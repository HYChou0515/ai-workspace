import { describe, expect, it } from "vitest";

import { extractToolImages } from "./toolImages";

describe("extractToolImages", () => {
  it("pulls sci-plot `images` paths out of the pretty-printed result", () => {
    const body = [
      "Tool `chart` returned (exit_code=0):",
      "{",
      '  "images": [',
      '    "charts/box_scatter_20260627-112304-583252.png"',
      "  ]",
      "}",
    ].join("\n");
    expect(extractToolImages(body)).toEqual(["charts/box_scatter_20260627-112304-583252.png"]);
  });

  it("also handles csv-column-summary's `plots` key with multiple files", () => {
    const body = '{"plots": ["a.distributions.png", "a.correlations.png"]}';
    expect(extractToolImages(body)).toEqual(["a.distributions.png", "a.correlations.png"]);
  });

  it("ignores non-image entries and de-dupes", () => {
    const body = '{"images": ["x.png", "notes.txt", "x.png", "y.svg"]}';
    expect(extractToolImages(body)).toEqual(["x.png", "y.svg"]);
  });

  it("returns [] for plain text / no payload / undefined", () => {
    expect(extractToolImages("Tool `exec` returned (exit_code=0):\nhello")).toEqual([]);
    expect(extractToolImages(undefined)).toEqual([]);
    expect(extractToolImages("")).toEqual([]);
  });

  it("returns [] when the array isn't valid JSON", () => {
    expect(extractToolImages('"images": [oops not json png]')).toEqual([]);
  });
});
