import { describe, expect, it } from "vitest";

import { quotaAmounts, quotaKind, quotaKinds, quotaMessage } from "./quotaFailure";
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

describe("quotaKinds — more than one limit at its cap", () => {
  // Telling someone about one limit at a time produces a sequence that reads as
  // a bug: they free disk, resend, and are told something different. Each
  // message was true; the first implied acting on it would let the turn through.
  it("names every limit the refusal listed, primary first", () => {
    expect(quotaKinds(507, "sandbox_quota_exceeded", ["workspace_quota_exceeded"])).toEqual([
      "environment",
      "workspace",
    ]);
  });

  it("is just the one when only one bound", () => {
    expect(quotaKinds(507, "user_quota_exceeded", undefined)).toEqual(["user"]);
  });

  it("stays empty for anything that is not a quota", () => {
    expect(quotaKinds(404, undefined, undefined)).toEqual([]);
  });
});


describe("quotaAmounts — the numbers behind the refusal", () => {
  // "Your workspace is full" is unfalsifiable to the person reading it: a wrong
  // one looks exactly like a right one until someone reads the code. It was
  // reported as a bug for that reason — the disk was empty and the message said
  // otherwise, and there was no way to tell from the screen which limit fired.
  it("reads bytes as sizes for the two disk limits", () => {
    expect(quotaAmounts({ error: "workspace_quota_exceeded", used: 0, quota: 1024 })).toEqual({
      used: "0 B",
      limit: "1.0 KB",
    });
    expect(quotaAmounts({ error: "user_quota_exceeded", used: 2048, quota: 4096 })).toEqual({
      used: "2.0 KB",
      limit: "4.0 KB",
    });
  });

  // The environment limit has three dimensions and they are not all bytes —
  // rendering cores as "1 B" would be worse than showing nothing.
  // The environment limit is three limits behind one sentence, so its numbers
  // have to say which one they are: "2 of 1" under "you are at your limit for
  // live environments" reads as environments even when it means cores.
  it("reads each environment dimension in its own unit, and names it", () => {
    expect(
      quotaAmounts({ error: "sandbox_quota_exceeded", dimension: "sandboxes", used: 2, limit: 1 }),
    ).toEqual({ used: "2", limit: "1", dimension: "sandboxes" });
    expect(
      quotaAmounts({ error: "sandbox_quota_exceeded", dimension: "cpu", used: 4, limit: 2 }),
    ).toEqual({ used: "4", limit: "2", dimension: "cpu" });
    expect(
      quotaAmounts({ error: "sandbox_quota_exceeded", dimension: "memory", used: 512, limit: 1024 }),
    ).toEqual({ used: "512 B", limit: "1.0 KB", dimension: "memory" });
  });

  it("leaves the two disk limits unnamed — their sentence is already specific", () => {
    expect(quotaAmounts({ error: "workspace_quota_exceeded", used: 0, quota: 1024 })).toEqual({
      used: "0 B",
      limit: "1.0 KB",
    });
  });

  it("is null when the body carried no numbers, so nothing is invented", () => {
    expect(quotaAmounts({ error: "workspace_quota_exceeded" })).toBeNull();
    expect(quotaAmounts(undefined)).toBeNull();
  });
});

describe("quotaMessage — one sentence, both surfaces", () => {
  const KEYS = {
    workspace: "chat.send.workspaceFull",
    user: "chat.send.userFull",
    environment: "chat.send.envFull",
  } as const;
  const t = (key: string, vars?: Record<string, unknown>) =>
    vars ? `${key}(${Object.values(vars).join("/")})` : key;

  it("names the limit, its numbers, and every other limit that bound", () => {
    expect(
      quotaMessage(t as never, KEYS as never, {
        status: 507,
        code: "sandbox_quota_exceeded",
        also: ["workspace_quota_exceeded"],
        detail: { error: "sandbox_quota_exceeded", dimension: "cpu", used: 4, limit: 2 },
      }),
      // the dimension leads the numbers: 4 CORES, not 4 environments
    ).toBe(
      "chat.send.envFull resources.usedOfLimitNamed(resources.gauge.cpu/4/2) resources.also.workspace",
    );
  });

  // Reading `detail` alone silently demoted every caller that only carries
  // `code` — which is all of them except the send path — to the code-less
  // default, i.e. the WRONG limit. The terminal's tests caught it.
  it("still names the right limit when the caller only has the code", () => {
    expect(
      quotaMessage(t as never, KEYS as never, { status: 507, code: "user_quota_exceeded" }),
    ).toBe("chat.send.userFull");
  });

  it("is null for a failure that is not a quota, so the real error survives", () => {
    expect(quotaMessage(t as never, KEYS as never, { status: 500 })).toBeNull();
  });
});