import { describe, expect, it } from "vitest";

import { parseKbDocHref } from "./kbLinks";

describe("parseKbDocHref", () => {
  it("extracts the document id from a kb:// doc uri", () => {
    expect(parseKbDocHref("kb://doc/col-1/alice/guide.md")).toBe("col-1/alice/guide.md");
  });

  it("strips a fragment", () => {
    expect(parseKbDocHref("kb://doc/col-1/alice/guide.md#section")).toBe("col-1/alice/guide.md");
  });

  it("returns null for external / relative / empty links", () => {
    expect(parseKbDocHref("https://example.com")).toBeNull();
    expect(parseKbDocHref("./other.md")).toBeNull();
    expect(parseKbDocHref("kb://doc/")).toBeNull();
  });

  it("decodes the percent-encoded ∕ (U+2215) the markdown renderer emits", () => {
    // The doc id separator ∕ is non-ASCII, so micromark percent-encodes it in
    // the href. parseKbDocHref must hand back the RAW id (downstream re-encodes
    // once); returning the encoded form double-encodes → /kb/documents 404.
    const raw = "collection:abc∕default-user∕01-feol∕01-substrate.md";
    const encoded = `kb://doc/${raw.replaceAll("∕", "%E2%88%95")}`;
    expect(parseKbDocHref(encoded)).toBe(raw);
  });
});
