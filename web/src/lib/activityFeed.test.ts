import { describe, expect, it } from "vitest";

import type { EntityCatalog } from "../api/entities";
import type { ActivityEntry } from "../api/types";
import { activityOpenTarget, filterItemActivity } from "./activityFeed";

const entry = (ref: ActivityEntry["ref"], text = "x"): ActivityEntry => ({
  ts: "2026-07-05T00:00:00Z",
  kind: "entity_created",
  text,
  ref,
});

const catalog: EntityCatalog = {
  types: [
    { name: "issue", records_path: "issues", fields: [], form: [] },
    { name: "milestone", records_path: "milestones", fields: [], form: [] },
  ],
  diagnostics: [],
};

describe("filterItemActivity", () => {
  it("keeps only entries whose ref points at the item, preserving order", () => {
    const entries = [
      entry({ investigation_id: "A", type: "issue", number: 1 }, "a1"),
      entry({ investigation_id: "B", type: "issue", number: 2 }, "b1"),
      entry({ investigation_id: "A", path: "/notes.md" }, "a2"),
    ];
    expect(filterItemActivity(entries, "A").map((e) => e.text)).toEqual(["a1", "a2"]);
  });

  it("is empty when nothing matches the item", () => {
    expect(filterItemActivity([entry({ investigation_id: "B" })], "A")).toEqual([]);
  });
});

describe("activityOpenTarget", () => {
  it("resolves an entity write to its record file via the catalog records_path", () => {
    expect(activityOpenTarget(entry({ investigation_id: "A", type: "issue", number: 7 }), catalog)).toBe("/issues/7.md");
  });

  it("prefers an explicit file path (file events)", () => {
    expect(activityOpenTarget(entry({ investigation_id: "A", path: "/report.md" }), catalog)).toBe("/report.md");
  });

  it("returns null for an entity type absent from the catalog", () => {
    expect(activityOpenTarget(entry({ investigation_id: "A", type: "ghost", number: 1 }), catalog)).toBeNull();
  });

  it("returns null when the catalog hasn't loaded yet", () => {
    expect(activityOpenTarget(entry({ investigation_id: "A", type: "issue", number: 1 }), undefined)).toBeNull();
  });

  it("returns null when the entry points at nothing openable", () => {
    expect(activityOpenTarget(entry({ investigation_id: "A" }), catalog)).toBeNull();
  });
});
