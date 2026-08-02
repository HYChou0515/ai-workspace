import { describe, expect, it } from "vitest";

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
