import { describe, expect, it } from "vitest";

import type { Investigation } from "../api/types";
import {
  countByStatus,
  criticalCount,
  filterByTab,
  openCount,
  ownedByCount,
  topicCounts,
  watchingCount,
} from "./home.helpers";

function make(partial: Partial<Investigation>): Investigation {
  return {
    resource_id: "INC-X",
    title: "",
    owner: "",
    description: "",
    severity: "P2",
    status: "triaging",
    product: "",
    members: [],
    topics: [],
    attached_agent_config_id: null,
    created_time: "2026-01-01T00:00:00Z",
    updated_time: "2026-01-01T00:00:00Z",
    ...partial,
  };
}

describe("openCount", () => {
  it("counts triaging + awaiting_review as open", () => {
    const items = [
      make({ status: "triaging" }),
      make({ status: "awaiting_review" }),
      make({ status: "resolved" }),
      make({ status: "abandoned" }),
    ];
    expect(openCount(items)).toBe(2);
  });
});

describe("criticalCount", () => {
  it("counts only P0/P1 that are still open", () => {
    const items = [
      make({ severity: "P0", status: "triaging" }),
      make({ severity: "P1", status: "awaiting_review" }),
      make({ severity: "P1", status: "resolved" }), // closed → not critical-open
      make({ severity: "P2", status: "triaging" }),
    ];
    expect(criticalCount(items)).toBe(2);
  });
});

describe("countByStatus", () => {
  it("breaks down by all four statuses", () => {
    const items = [
      make({ status: "triaging" }),
      make({ status: "triaging" }),
      make({ status: "awaiting_review" }),
      make({ status: "resolved" }),
      make({ status: "abandoned" }),
    ];
    expect(countByStatus(items)).toEqual({
      triaging: 2,
      awaiting_review: 1,
      resolved: 1,
      abandoned: 1,
    });
  });
});

describe("ownedByCount / watchingCount", () => {
  const items = [
    make({ owner: "alice", status: "triaging" }),
    make({ owner: "alice", status: "resolved" }), // closed → excluded
    make({ owner: "bob", members: ["alice"], status: "awaiting_review" }),
    make({ owner: "bob", members: ["alice"], status: "abandoned" }), // closed → excluded from watching
  ];

  it("ownedByCount counts open investigations owned by user", () => {
    expect(ownedByCount(items, "alice")).toBe(1);
  });

  it("watchingCount counts open investigations where user is in members", () => {
    expect(watchingCount(items, "alice")).toBe(1);
  });
});

describe("topicCounts", () => {
  it("groups by topic with total and active counts", () => {
    const items = [
      make({ topics: ["SMT 1"], status: "triaging" }),
      make({ topics: ["SMT 1", "MX-7"], status: "resolved" }),
      make({ topics: ["MX-7"], status: "triaging" }),
    ];
    const counts = topicCounts(items);
    expect(counts.get("SMT 1")).toEqual({ total: 2, active: 1 });
    // MX-7: 1 resolved (closed) + 1 triaging (open) = total 2, active 1
    expect(counts.get("MX-7")).toEqual({ total: 2, active: 1 });
  });
});

describe("filterByTab", () => {
  const items = [
    make({ resource_id: "A", owner: "alice", status: "triaging" }),
    make({
      resource_id: "B",
      owner: "bob",
      members: ["alice"],
      status: "awaiting_review",
    }),
    make({ resource_id: "C", owner: "carla", status: "resolved" }),
  ];

  it("'all' returns everything", () => {
    expect(filterByTab(items, "all", "alice").length).toBe(3);
  });

  it("'my_open' returns only open investigations owned by current user", () => {
    expect(filterByTab(items, "my_open", "alice").map((i) => i.resource_id)).toEqual(["A"]);
  });

  it("'watching' returns open investigations where user is in members", () => {
    expect(filterByTab(items, "watching", "alice").map((i) => i.resource_id)).toEqual(["B"]);
  });

  it("'resolved' returns only resolved", () => {
    expect(filterByTab(items, "resolved", "alice").map((i) => i.resource_id)).toEqual(["C"]);
  });
});
