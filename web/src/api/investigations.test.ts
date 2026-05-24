import { describe, expect, it } from "vitest";

import { mockApi } from "./mock";

describe("investigations (mock client)", () => {
  it("updateInvestigation edits title/description/severity/product/topics", async () => {
    const inv = await mockApi.createInvestigation({ title: "Old" });
    await mockApi.updateInvestigation(inv.resource_id, {
      title: "New title",
      description: "revised brief",
      severity: "P0",
      product: "MX-7 board",
      topics: ["reflow", "void"],
    });
    const got = await mockApi.getInvestigation(inv.resource_id);
    expect(got.title).toBe("New title");
    expect(got.description).toBe("revised brief");
    expect(got.severity).toBe("P0");
    expect(got.product).toBe("MX-7 board");
    expect(got.topics).toEqual(["reflow", "void"]);
  });
});
