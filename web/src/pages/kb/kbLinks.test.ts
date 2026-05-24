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
});
