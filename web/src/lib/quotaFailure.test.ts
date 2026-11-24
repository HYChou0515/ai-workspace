import { describe, expect, it } from "vitest";

import { quotaKind } from "./quotaFailure";

describe("quotaKind", () => {
  // docs/plan-archive-full.md review round 1 (D-5): the archive's refusals also
  // answer 507, and no surface may read them as a full workspace — that sends
  // people off to delete files that are not the problem.
  it("is no quota for the archive's refusals", () => {
    expect(quotaKind(507, "archive_write_failed")).toBeNull();
    expect(quotaKind(507, "archive_space_low")).toBeNull();
  });

  it("still reads the limits it always read", () => {
    expect(quotaKind(507, "user_quota_exceeded")).toBe("user");
    expect(quotaKind(507, "sandbox_quota_exceeded")).toBe("environment");
    expect(quotaKind(507, undefined)).toBe("workspace");
    expect(quotaKind(500, "archive_space_low")).toBeNull();
  });
});
