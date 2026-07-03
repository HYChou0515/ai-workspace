import { describe, expect, it } from "vitest";

import { pickRenderer } from "./registry";

describe("registry — #419 entity view files", () => {
  it("routes a `*.ai.yaml` view to the entity renderer, ahead of generic yaml", () => {
    expect(pickRenderer("/views/board.ai.yaml")).toBe("aiview");
    expect(pickRenderer("/views/table.ai.yml")).toBe("aiview");
  });

  it("leaves ordinary yaml on the structured yaml renderer", () => {
    expect(pickRenderer("/config.yaml")).toBe("yaml");
    expect(pickRenderer("/data.yml")).toBe("yaml");
  });
});
