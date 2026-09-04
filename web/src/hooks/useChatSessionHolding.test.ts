/**
 * The closable list has to survive the trip from the 507 body to the screen.
 *
 * Adversarial review, finding 8: the backend emits `holding`, `quotaHolding`
 * parses it, `QuotaHoldingList` renders it — and nothing joined them, so the
 * whole of §1.8 ("撞牆的那一刻要是一扇門") existed only in unit tests. Components
 * with tests and no call site are the shape of a feature that reads as done and
 * is invisible.
 *
 * This pins the join at the one place the structured body still exists:
 * `useChatSession` reduces a send refusal to a STRING, and everything after
 * that point has thrown the list away.
 */

import { describe, expect, it } from "vitest";

import { holdingFromSendError } from "./useChatSessionHolding";

describe("holdingFromSendError", () => {
  it("keeps the closable environments out of a 507 body", () => {
    const got = holdingFromSendError({
      status: 507,
      detail: {
        error: "sandbox_quota_exceeded",
        dimension: "cpu",
        used: 4,
        limit: 4,
        holding: [{ item_id: "i-1", title: "晶圓良率分析", cpu_cores: 2, memory_bytes: 0 }],
      },
    });

    expect(got.map((h) => h.itemId)).toEqual(["i-1"]);
    expect(got[0].title).toBe("晶圓良率分析");
  });

  it("is empty for a refusal the reader may not see the inventory of", () => {
    // A collaborator hitting the owner's ceiling. The backend already withheld
    // it; the client must render that as no list rather than as a failure.
    const got = holdingFromSendError({
      status: 507,
      detail: { error: "sandbox_quota_exceeded", dimension: "cpu", holding: [] },
    });

    expect(got).toEqual([]);
  });

  it("is empty for every other kind of failure", () => {
    expect(holdingFromSendError({ status: 500 })).toEqual([]);
    expect(holdingFromSendError(new Error("boom"))).toEqual([]);
    expect(holdingFromSendError(null)).toEqual([]);
    expect(holdingFromSendError(undefined)).toEqual([]);
  });

  it("is empty for a disk refusal, which has no environment to close", () => {
    expect(
      holdingFromSendError({
        status: 507,
        detail: { error: "workspace_quota_exceeded", used: 10, quota: 10 },
      }),
    ).toEqual([]);
  });
});
