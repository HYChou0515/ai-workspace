import { describe, expect, it } from "vitest";

import { quotaKind } from "./quotaFailure";
import { uploadFailureKey } from "./uploadFailure";

describe("uploadFailureKey", () => {
  it("tells the three 507s apart", () => {
    // The regression: all three used to render as "this workspace is full",
    // which is actively wrong for two of them — the space to free may be in a
    // different item, and the third is not about files at all.
    expect(uploadFailureKey(507, "workspace_quota_exceeded")).toBe("workspace.upload.full");
    expect(uploadFailureKey(507, "user_quota_exceeded")).toBe("workspace.upload.userFull");
    expect(uploadFailureKey(507, "sandbox_quota_exceeded")).toBe("workspace.upload.envFull");
  });

  it("falls back to the workspace message when the server sent no code", () => {
    // An older backend, or a body that was not JSON. The pre-existing #245
    // behaviour is the safe default.
    expect(uploadFailureKey(507, undefined)).toBe("workspace.upload.full");
  });

  it("keeps the size cap and the unattributed failure distinct", () => {
    expect(uploadFailureKey(413, undefined)).toBe("workspace.upload.failed");
    expect(uploadFailureKey(403, undefined)).toBe("workspace.upload.error");
    expect(uploadFailureKey(undefined, undefined)).toBe("workspace.upload.error");
  });
});

describe("quotaKind", () => {
  it("names which of the three limits answered", () => {
    // The review's finding: three rules share 507 and need three remedies —
    // delete here / delete in another item / close an environment.
    expect(quotaKind(507, "workspace_quota_exceeded")).toBe("workspace");
    expect(quotaKind(507, "user_quota_exceeded")).toBe("user");
    expect(quotaKind(507, "sandbox_quota_exceeded")).toBe("environment");
  });

  it("treats a code-less 507 as the per-workspace one", () => {
    // An older backend, or a non-JSON body: fall back to what 507 meant before
    // the other two limits existed.
    expect(quotaKind(507, undefined)).toBe("workspace");
  });

  it("is null for anything that is not a quota", () => {
    expect(quotaKind(413, undefined)).toBeNull();
    expect(quotaKind(undefined, "user_quota_exceeded")).toBeNull();
  });
});
