import { describe, expect, it } from "vitest";

import { parseCsv, serializeCsv } from "./csv";

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

describe("serializeCsv", () => {
  it("writes rows as delimited lines ending in a newline", () => {
    expect(
      serializeCsv([
        ["a", "b"],
        ["1", "2"],
      ]),
    ).toBe("a,b\n1,2\n");
  });

  it("round-trips an unedited file byte-identically, CRLF and no trailing newline included", () => {
    // The grid opens a file, parses it, and writes it back on save. If that
    // path is not byte-identical, merely OPENING a file rewrites it — a
    // whole-file diff nobody asked for.
    const original = 'wafer,note\r\nW01,"Smith, J"\r\nW02,plain';
    expect(serializeCsv(parseCsv(original), ",", original)).toBe(original);
  });

  it("quotes only fields holding the delimiter, a quote or a newline", () => {
    expect(
      serializeCsv([
        ["plain", "Smith, J", 'says "hi"', "two\nlines"],
      ]),
    ).toBe('plain,"Smith, J","says ""hi""","two\nlines"\n');
  });
});
