/**
 * `viewDocument(spec)` — the WHOLE parsed `.ai.yaml` a view was opened with
 * (#847/#848, requested by the chart plugin, #855). `viewParam` reads keys the
 * plugin already knows; a plugin that rejects unknown keys (a typo must be a
 * loud error) needs to see all of them.
 */
import { describe, expect, it } from "vitest";

import { viewDocument } from "./public";
import { parseViewSpec } from "./shared";

describe("viewDocument", () => {
  it("is the document as written, platform-colliding and unknown keys included", () => {
    const spec = parseViewSpec("view: chart\ntitle: Yield\nsource: data/y.csv\ncolumns: [a, b]\nmrak: bar\n");
    expect(spec).not.toBeNull();
    expect(viewDocument(spec!)).toEqual({
      view: "chart",
      title: "Yield",
      source: "data/y.csv",
      columns: ["a", "b"],
      mrak: "bar",
    });
  });

  it("is a copy: a plugin editing it cannot change the spec the platform holds", () => {
    const spec = parseViewSpec("view: chart\nencoding:\n  x: {field: lot}\n")!;
    const doc = viewDocument(spec) as { encoding: { x: { field: string } } };
    doc.encoding.x.field = "changed";
    expect((viewDocument(spec) as typeof doc).encoding.x.field).toBe("lot");
  });

  it("is empty for a spec built without a document", () => {
    expect(viewDocument({ view: "chart" } as never)).toEqual({});
  });
});
