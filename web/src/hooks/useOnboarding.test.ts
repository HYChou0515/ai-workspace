// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import type { Onboarding } from "../api/types";
import { dismiss } from "../lib/onboarding";
import { useOnboarding } from "./useOnboarding";

const C: Onboarding = { version: "1", title: "Welcome", intro: "hi", points: [] };
const ALICE = { id: "alice", ready: true };

describe("useOnboarding gate", () => {
  beforeEach(() => localStorage.clear());

  it("auto-opens when there is content and nothing dismissed", () => {
    const { result } = renderHook(() => useOnboarding(ALICE, "rca", C));
    expect(result.current.open).toBe(true);
  });

  it("stays closed when there is no content", () => {
    const { result } = renderHook(() => useOnboarding(ALICE, "rca", undefined));
    expect(result.current.open).toBe(false);
  });

  it("does not auto-open once the current version is dismissed", () => {
    renderHook(() => useOnboarding(ALICE, "rca", C)).result.current.dontShowAgain();
    const { result } = renderHook(() => useOnboarding(ALICE, "rca", C));
    expect(result.current.open).toBe(false);
  });

  it("auto-opens again after the content version is bumped", () => {
    const first = renderHook(() => useOnboarding(ALICE, "rca", C));
    act(() => first.result.current.dontShowAgain());
    const bumped: Onboarding = { ...C, version: "2" };
    const { result } = renderHook(() => useOnboarding(ALICE, "rca", bumped));
    expect(result.current.open).toBe(true);
  });

  it("'Got it' closes without persisting — it shows again next mount", () => {
    const first = renderHook(() => useOnboarding(ALICE, "rca", C));
    act(() => first.result.current.gotIt());
    expect(first.result.current.open).toBe(false);
    const { result } = renderHook(() => useOnboarding(ALICE, "rca", C));
    expect(result.current.open).toBe(true);
  });

  it("reopen() shows it manually even after a permanent dismiss", () => {
    const { result } = renderHook(() => useOnboarding(ALICE, "rca", C));
    act(() => result.current.dontShowAgain());
    expect(result.current.open).toBe(false);
    act(() => result.current.reopen());
    expect(result.current.open).toBe(true);
  });

  it("never opens for someone who dismissed it, though their id arrives after the first paint", () => {
    // A cold load knows nobody yet: the id is the "default-user" placeholder
    // until the current-user query settles. Judging dismissal on it opened the
    // modal for everyone, and nothing closed it when the real id arrived.
    dismiss("alice", "rca", "1");
    const { result, rerender } = renderHook(({ user }) => useOnboarding(user, "rca", C), {
      initialProps: { user: { id: "default-user", ready: false } },
    });
    expect(result.current.open).toBe(false);
    rerender({ user: ALICE });
    expect(result.current.open).toBe(false);
  });

  it("opens once the user is known, for someone who has not dismissed it", () => {
    const { result, rerender } = renderHook(({ user }) => useOnboarding(user, "rca", C), {
      initialProps: { user: { id: "default-user", ready: false } },
    });
    expect(result.current.open).toBe(false);
    rerender({ user: ALICE });
    expect(result.current.open).toBe(true);
  });
});
