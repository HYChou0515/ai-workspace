import { describe, expect, it } from "vitest";

import { parseCsv } from "./csv";

describe("parseCsv", () => {
  it("splits rows and comma-separated cells", () => {
    expect(parseCsv("a,b\n1,2\n")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });

  it("handles quoted fields with commas and escaped quotes", () => {
    expect(parseCsv('name,note\n"Smith, J","says ""hi"""\n')).toEqual([
      ["name", "note"],
      ["Smith, J", 'says "hi"'],
    ]);
  });

  it("handles CRLF line endings and a missing trailing newline", () => {
    expect(parseCsv("x\r\ny")).toEqual([["x"], ["y"]]);
  });
});
