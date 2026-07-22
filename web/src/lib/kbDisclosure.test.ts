// @vitest-environment happy-dom
/** #605: sticky per-chat "disclose withheld sources" toggle. */
import { beforeEach, describe, expect, it } from "vitest";

import { getKbDisclosure, setKbDisclosure } from "./kbDisclosure";

describe("kbDisclosure", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to ON — a fresh browser discloses withheld sources", () => {
    expect(getKbDisclosure()).toBe(true);
  });

  it("round-trips OFF (the faster pick)", () => {
    setKbDisclosure(false);
    expect(getKbDisclosure()).toBe(false);
  });

  it("round-trips back ON", () => {
    setKbDisclosure(false);
    setKbDisclosure(true);
    expect(getKbDisclosure()).toBe(true);
  });

  it("treats stored garbage as the default", () => {
    localStorage.setItem("rca.kbDisclosure", "banana");
    expect(getKbDisclosure()).toBe(true);
  });
});
