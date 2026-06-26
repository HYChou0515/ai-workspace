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

  it("#255: splits on a tab delimiter for TSV files", () => {
    expect(parseCsv("a\tb\n1\t2\n", "\t")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });

  it("#255: with a tab delimiter, commas are literal cell text", () => {
    expect(parseCsv("city,state\tpop\nTaipei,TW\t2.6M\n", "\t")).toEqual([
      ["city,state", "pop"],
      ["Taipei,TW", "2.6M"],
    ]);
  });
});
