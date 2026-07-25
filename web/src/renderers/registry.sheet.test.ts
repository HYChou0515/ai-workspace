import { describe, expect, it } from "vitest";

import { pickRenderer } from "./registry";

describe("registry — *.ai.csv spreadsheet files", () => {
  it("routes a `*.ai.csv` / `*.ai.tsv` to the sheet renderer, ahead of the read-only csv preview", () => {
    expect(pickRenderer("/data/wafers.ai.csv")).toBe("sheet");
    expect(pickRenderer("/data/wafers.ai.tsv")).toBe("sheet");
  });

  it("leaves a plain csv/tsv on the read-only csv preview", () => {
    expect(pickRenderer("/data/wafers.csv")).toBe("csv");
    expect(pickRenderer("/data/wafers.tsv")).toBe("csv");
  });

  it("does not capture an entity view file", () => {
    expect(pickRenderer("/views/board.ai.yaml")).toBe("aiview");
  });
});
