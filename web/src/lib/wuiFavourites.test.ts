// @vitest-environment happy-dom
/**
 * The viewer's favourites on the WUI overview — a set of page keys in
 * localStorage, per signed-in user (`docs/plan-wui-overview-icon-favourites.md`).
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { favouriteKey, readFavourites, toggleFavourite, useWuiFavourites } from "./wuiFavourites";

const KEY = "rca.wuiFavourites";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("favouriteKey", () => {
  it("is the row's identity — item and path, the halves of the deploy id", () => {
    expect(favouriteKey({ item_id: "i-1", path: "/pages/report/page.ai.yaml" })).toBe(
      "i-1/pages/report/page.ai.yaml",
    );
  });
});

describe("readFavourites / toggleFavourite", () => {
  it("reads nothing, garbage and a non-object as empty", () => {
    expect(readFavourites("alice")).toEqual([]);
    localStorage.setItem(KEY, "not json");
    expect(readFavourites("alice")).toEqual([]);
    localStorage.setItem(KEY, JSON.stringify([1, 2]));
    expect(readFavourites("alice")).toEqual([]);
    localStorage.setItem(KEY, JSON.stringify({ alice: "not a list" }));
    expect(readFavourites("alice")).toEqual([]);
  });

  it("toggles a key on and off, and a second toggle on is a no-op on storage", () => {
    toggleFavourite("alice", "k1");
    expect(readFavourites("alice")).toEqual(["k1"]);
    toggleFavourite("alice", "k2");
    expect(readFavourites("alice")).toEqual(["k1", "k2"]);
    toggleFavourite("alice", "k1");
    expect(readFavourites("alice")).toEqual(["k2"]);
    // Off twice is off; the stored list never holds a key twice.
    toggleFavourite("alice", "k1");
    toggleFavourite("alice", "k1");
    toggleFavourite("alice", "k1");
    expect(readFavourites("alice")).toEqual(["k2", "k1"]);
  });

  it("keeps one person's stars apart from another's on the same browser", () => {
    toggleFavourite("alice", "k1");
    toggleFavourite("bob", "k2");
    expect(readFavourites("alice")).toEqual(["k1"]);
    expect(readFavourites("bob")).toEqual(["k2"]);
    // Ids that would collide once joined with a separator, or that contain
    // the escape character itself, stay distinct — the `onboarding.ts` rule.
    toggleFavourite("a:b", "k3");
    toggleFavourite("a%3Ab", "k4");
    expect(readFavourites("a:b")).toEqual(["k3"]);
    expect(readFavourites("a%3Ab")).toEqual(["k4"]);
  });

  it("survives a storage that throws — a star just is not sticky", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readFavourites("alice")).toEqual([]);
    expect(() => toggleFavourite("alice", "k1")).not.toThrow();
  });
});

describe("useWuiFavourites", () => {
  it("answers from storage on first render and re-renders on toggle, writing through", () => {
    toggleFavourite("alice", "k1");
    const { result } = renderHook(() => useWuiFavourites("alice"));
    expect(result.current.has("k1")).toBe(true);
    expect(result.current.has("k2")).toBe(false);

    act(() => result.current.toggle("k2"));

    expect(result.current.has("k2")).toBe(true);
    expect(readFavourites("alice")).toEqual(["k1", "k2"]);

    act(() => result.current.toggle("k1"));

    expect(result.current.has("k1")).toBe(false);
    expect(readFavourites("alice")).toEqual(["k2"]);
  });

  it("follows the user it is asked about, not the one it was first rendered with", () => {
    toggleFavourite("alice", "k1");
    toggleFavourite("bob", "k2");
    const { result, rerender } = renderHook(({ user }) => useWuiFavourites(user), {
      initialProps: { user: "alice" },
    });
    expect(result.current.has("k1")).toBe(true);
    rerender({ user: "bob" });
    expect(result.current.has("k1")).toBe(false);
    expect(result.current.has("k2")).toBe(true);
  });
});
