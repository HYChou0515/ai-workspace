import { describe, expect, it } from "vitest";

import { decodeLeafPath, encodeLeafPath } from "./leafPath";

describe("leafPath (#93 documents/wiki splat encoding)", () => {
  it("encodes a canonical path into a slash-preserving splat with encoded segments", () => {
    expect(encodeLeafPath("/dir/x.md")).toBe("dir/x.md");
    // a space is percent-encoded WITHIN the segment; the slash stays a separator
    expect(encodeLeafPath("/a dir/b.md")).toBe("a%20dir/b.md");
  });

  it("decodes a splat back to the canonical leading-slash path", () => {
    // react-router hands back an already-decoded splat — just re-add the slash
    expect(decodeLeafPath("dir/x.md")).toBe("/dir/x.md");
    expect(decodeLeafPath("a dir/b.md")).toBe("/a dir/b.md");
  });

  it("round-trips a nested, spaced path (encode → decode is identity)", () => {
    const path = "/a dir/nested/b c.md";
    // the encoded form is what the URL carries; react-router decodes it before
    // handing it back as the splat, so decode(decoded-encode) === the original
    expect(decodeLeafPath(decodeURIComponent(encodeLeafPath(path)))).toBe(path);
  });
});
