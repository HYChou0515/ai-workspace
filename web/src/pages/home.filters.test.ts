import { describe, expect, it } from "vitest";

import type { Investigation } from "../api/types";
import {
  EMPTY_FILTERS,
  applyFilters,
  isFiltersEmpty,
  ownersOf,
  sortBy,
  togglePick,
  topicsOf,
} from "./home.helpers";

function make(partial: Partial<Investigation>): Investigation {
  return {
    resource_id: "INC-2026-0001",
    title: "",
    owner: "",
    description: "",
    severity: "P2",
    status: "triaging",
    product: "",
    members: [],
    topics: [],
    attached_agent_config_id: null,
    created_time: "2026-05-01T00:00:00Z",
    updated_time: "2026-05-01T00:00:00Z",
    ...partial,
  };
}

describe("applyFilters", () => {
  const items = [
    make({ resource_id: "INC-1", title: "Reflow drift", severity: "P1", owner: "alice", topics: ["SMT 1"] }),
    make({ resource_id: "INC-2", title: "BGA voids", severity: "P0", owner: "bob", topics: ["X-ray void"] }),
    make({ resource_id: "INC-3", title: "Conformal coat", severity: "P3", owner: "alice", topics: ["MX-7"] }),
  ];

  it("returns all items when filters are empty", () => {
    expect(applyFilters(items, EMPTY_FILTERS)).toHaveLength(3);
  });

  it("query matches title (case-insensitive)", () => {
    const got = applyFilters(items, { ...EMPTY_FILTERS, query: "REFLOW" });
    expect(got.map((i) => i.resource_id)).toEqual(["INC-1"]);
  });

  it("query matches the short formatted id", () => {
    // formatInvestigationId("INC-2") → "INC2" (dashes stripped, first 8)
    const got = applyFilters(items, { ...EMPTY_FILTERS, query: "INC2" });
    expect(got.map((i) => i.resource_id)).toEqual(["INC-2"]);
  });

  it("multi-severity is OR within the picker", () => {
    const got = applyFilters(items, { ...EMPTY_FILTERS, severities: ["P0", "P1"] });
    expect(got.map((i) => i.resource_id).sort()).toEqual(["INC-1", "INC-2"]);
  });

  it("owners filter narrows to matching owners", () => {
    const got = applyFilters(items, { ...EMPTY_FILTERS, owners: ["alice"] });
    expect(got.map((i) => i.resource_id).sort()).toEqual(["INC-1", "INC-3"]);
  });

  it("topics filter is any-of (investigation has at least one selected topic)", () => {
    const got = applyFilters(items, { ...EMPTY_FILTERS, topics: ["X-ray void", "SMT 1"] });
    expect(got.map((i) => i.resource_id).sort()).toEqual(["INC-1", "INC-2"]);
  });

  it("combining filters AND-narrows", () => {
    const got = applyFilters(items, {
      ...EMPTY_FILTERS,
      query: "conformal",
      severities: ["P3"],
      owners: ["alice"],
    });
    expect(got.map((i) => i.resource_id)).toEqual(["INC-3"]);
  });
});

describe("sortBy", () => {
  const items = [
    make({ resource_id: "B", title: "Beta", severity: "P3", updated_time: "2026-05-10T00:00:00Z" }),
    make({ resource_id: "A", title: "Alpha", severity: "P1", updated_time: "2026-05-20T00:00:00Z" }),
    make({ resource_id: "C", title: "Gamma", severity: "P2", updated_time: "2026-05-15T00:00:00Z" }),
  ];

  it("sorts by updated_time desc by default", () => {
    expect(sortBy(items, "updated").map((i) => i.resource_id)).toEqual(["A", "C", "B"]);
  });

  it("sorts by severity desc puts P0/P1 first", () => {
    expect(sortBy(items, "severity").map((i) => i.severity)).toEqual(["P1", "P2", "P3"]);
  });

  it("respects asc direction", () => {
    expect(sortBy(items, "updated", "asc").map((i) => i.resource_id)).toEqual(["B", "C", "A"]);
  });

  it("pins float to the top regardless of sort", () => {
    const pinned = new Set(["B"]);
    expect(sortBy(items, "updated", "desc", pinned).map((i) => i.resource_id)).toEqual([
      "B",
      "A",
      "C",
    ]);
  });
});

describe("togglePick", () => {
  it("adds when absent, removes when present", () => {
    expect(togglePick(["a"], "b")).toEqual(["a", "b"]);
    expect(togglePick(["a", "b"], "a")).toEqual(["b"]);
  });
});

describe("ownersOf / topicsOf", () => {
  const items = [
    make({ owner: "carla", topics: ["SMT 2", "Reflow"] }),
    make({ owner: "alice", topics: ["SMT 1"] }),
    make({ owner: "alice", topics: ["SMT 1", "Stencil"] }),
  ];

  it("returns distinct sorted owners", () => {
    expect(ownersOf(items)).toEqual(["alice", "carla"]);
  });

  it("returns distinct sorted topics", () => {
    expect(topicsOf(items)).toEqual(["Reflow", "SMT 1", "SMT 2", "Stencil"]);
  });
});

describe("isFiltersEmpty", () => {
  it("true for the canonical empty value", () => {
    expect(isFiltersEmpty(EMPTY_FILTERS)).toBe(true);
  });

  it("false if any field has content", () => {
    expect(isFiltersEmpty({ ...EMPTY_FILTERS, query: "x" })).toBe(false);
    expect(isFiltersEmpty({ ...EMPTY_FILTERS, severities: ["P0"] })).toBe(false);
  });
});
