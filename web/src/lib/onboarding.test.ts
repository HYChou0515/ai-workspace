// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from "vitest";

import { dismiss, dismissedVersion, isDismissed } from "./onboarding";

describe("onboarding dismissal store", () => {
  beforeEach(() => localStorage.clear());

  it("nothing is dismissed initially", () => {
    expect(isDismissed("alice", "platform", "1")).toBe(false);
    expect(dismissedVersion("alice", "platform")).toBeNull();
  });

  it("dismiss pins the exact version for that user+scope", () => {
    dismiss("alice", "rca", "1");
    expect(isDismissed("alice", "rca", "1")).toBe(true);
    expect(dismissedVersion("alice", "rca")).toBe("1");
  });

  it("a bumped version is no longer considered dismissed", () => {
    dismiss("alice", "rca", "1");
    expect(isDismissed("alice", "rca", "2")).toBe(false);
  });

  it("scopes dismissal per user and per scope", () => {
    dismiss("alice", "rca", "1");
    expect(isDismissed("bob", "rca", "1")).toBe(false); // other user
    expect(isDismissed("alice", "platform", "1")).toBe(false); // other scope
  });

  it("survives a userId containing the separator char", () => {
    dismiss("a:b", "rca", "1");
    expect(isDismissed("a:b", "rca", "1")).toBe(true);
    expect(isDismissed("a", "b:rca", "1")).toBe(false); // no key collision
  });
});
