/**
 * The people picker's "picked often" memory (docs/plan-people-picker-ranking.md
 * D2): per signed-in person, in this browser, how often and how recently each
 * person was picked.
 */
import { describe, expect, it } from "vitest";

import { readFrecency, recordPick, removePick } from "./peopleFrecency";

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.UTC(2026, 10, 7);

/** An in-memory `localStorage` stand-in, so each test starts empty. */
function memory(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    clear: () => m.clear(),
    key: (i: number) => [...m.keys()][i] ?? null,
    get length() {
      return m.size;
    },
  };
}

describe("peopleFrecency", () => {
  it("knows no one at first", () => {
    expect(readFrecency("me", NOW, memory()).size).toBe(0);
  });

  it("scores a pick by how long ago it was", () => {
    const s = memory();
    recordPick("me", "a", NOW - 1 * DAY, s);
    recordPick("me", "b", NOW - 10 * DAY, s);
    recordPick("me", "c", NOW - 20 * DAY, s);
    recordPick("me", "d", NOW - 60 * DAY, s);
    recordPick("me", "e", NOW - 200 * DAY, s);
    expect(Object.fromEntries(readFrecency("me", NOW, s))).toEqual({ a: 100, b: 70, c: 50, d: 30, e: 10 });
  });

  it("adds up the picks of one person, so often beats once", () => {
    const s = memory();
    recordPick("me", "often", NOW - 40 * DAY, s);
    recordPick("me", "often", NOW - 35 * DAY, s);
    recordPick("me", "often", NOW - 32 * DAY, s);
    recordPick("me", "once", NOW - 20 * DAY, s);
    const f = readFrecency("me", NOW, s);
    expect(f.get("often")).toBe(90);
    expect(f.get("once")).toBe(50);
  });

  it("keeps a person's last 10 picks", () => {
    const s = memory();
    for (let i = 0; i < 15; i++) recordPick("me", "a", NOW, s);
    expect(readFrecency("me", NOW, s).get("a")).toBe(1000);
  });

  it("remembers at most 20 people, forgetting the one scored lowest", () => {
    const s = memory();
    recordPick("me", "old", NOW - 300 * DAY, s);
    for (let i = 0; i < 19; i++) recordPick("me", `p${i}`, NOW, s);
    recordPick("me", "new", NOW, s);
    const f = readFrecency("me", NOW, s);
    expect(f.size).toBe(20);
    expect(f.has("old")).toBe(false);
    expect(f.has("new")).toBe(true);
  });

  it("keeps each signed-in person's own list", () => {
    const s = memory();
    recordPick("me", "a", NOW, s);
    expect(readFrecency("someone-else", NOW, s).size).toBe(0);
  });

  it("forgets a person on request", () => {
    const s = memory();
    recordPick("me", "a", NOW, s);
    recordPick("me", "b", NOW, s);
    removePick("me", "a", s);
    expect([...readFrecency("me", NOW, s).keys()]).toEqual(["b"]);
  });

  it("treats unreadable storage as no memory, and a failed write as nothing", () => {
    const broken = {
      ...memory(),
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("quota");
      },
    } as Storage;
    expect(readFrecency("me", NOW, broken).size).toBe(0);
    expect(() => recordPick("me", "a", NOW, broken)).not.toThrow();
    expect(() => removePick("me", "a", broken)).not.toThrow();
  });

  it("treats a corrupt record as no memory", () => {
    const s = memory();
    s.setItem("peoplePicker.frecency.me", "{not json");
    expect(readFrecency("me", NOW, s).size).toBe(0);
    s.setItem("peoplePicker.frecency.me", JSON.stringify({ a: "nope", b: [NOW] }));
    expect(Object.fromEntries(readFrecency("me", NOW, s))).toEqual({ b: 100 });
  });

  it("survives a browser where even reaching localStorage throws", () => {
    // Blocked site data makes the `localStorage` property itself throw, so the
    // default storage must be reached inside the guard, not as a default value.
    const real = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      get() {
        throw new DOMException("denied", "SecurityError");
      },
    });
    try {
      expect(readFrecency("me", NOW).size).toBe(0);
      expect(() => recordPick("me", "a", NOW)).not.toThrow();
      expect(() => removePick("me", "a")).not.toThrow();
    } finally {
      if (real) Object.defineProperty(globalThis, "localStorage", real);
    }
  });
});

