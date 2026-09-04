import { describe, expect, it } from "vitest";

import { createSelfWrites } from "./selfWrites";

describe("createSelfWrites", () => {
  it("swallows the echo of a write this page made", () => {
    // Found by running one: every save came back as "somebody else changed
    // this", which discredits the warning that matters.
    const s = createSelfWrites();
    s.record("/sales/data.json");

    expect(s.consume("/sales/data.json")).toBe(true);
  });

  it("lets somebody else's edit through", () => {
    const s = createSelfWrites();

    expect(s.consume("/sales/data.json")).toBe(false);
  });

  it("swallows one echo per write, not one per path", () => {
    // Two saves in flight produce two events; suppressing on "was there a
    // recent write" would let the second one through as a false alarm.
    const s = createSelfWrites();
    s.record("/a.json");
    s.record("/a.json");

    expect(s.consume("/a.json")).toBe(true);
    expect(s.consume("/a.json")).toBe(true);
    expect(s.consume("/a.json")).toBe(false);
  });

  it("keeps paths apart", () => {
    const s = createSelfWrites();
    s.record("/a.json");

    expect(s.consume("/b.json")).toBe(false);
    expect(s.consume("/a.json")).toBe(true);
  });

  it("forgets a write whose echo never came, rather than muting that path", () => {
    // A failed write or a dropped stream would otherwise leave one suppression
    // armed for the life of the page, and it would land on a real edit.
    let now = 1000;
    const s = createSelfWrites(() => now, 5000);
    s.record("/a.json");

    now += 6000;

    expect(s.consume("/a.json")).toBe(false);
  });
});
