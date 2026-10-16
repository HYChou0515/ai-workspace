import { describe, expect, it } from "vitest";

import { createLimiter, mapWithConcurrency } from "./concurrency";

const tick = () => new Promise((r) => setTimeout(r, 0));

describe("createLimiter", () => {
  it("runs at most `limit` calls at once and starts the next as one ends", async () => {
    const run = createLimiter(2);
    const ends: Array<() => void> = [];
    const started: number[] = [];
    const calls = [0, 1, 2, 3].map((i) =>
      run(() => {
        started.push(i);
        return new Promise<number>((resolve) => ends.push(() => resolve(i)));
      }),
    );
    await tick();
    expect(started).toEqual([0, 1]);

    ends[0]();
    await tick();
    expect(started).toEqual([0, 1, 2]);
    ends[1]();
    ends[2]();
    await tick();
    ends[3]();
    expect(await Promise.all(calls)).toEqual([0, 1, 2, 3]);
  });

  it("drops a waiting call whose signal aborts — it never starts and takes no slot", async () => {
    const run = createLimiter(1);
    let end: () => void = () => {};
    const first = run(() => new Promise<void>((resolve) => (end = resolve)));
    const abort = new AbortController();
    let ran = false;
    const waiting = run(async () => {
      ran = true;
    }, abort.signal);
    const third = run(async () => "third");

    abort.abort();
    await expect(waiting).rejects.toThrow();
    end();
    await first;

    expect(await third).toBe("third");
    expect(ran).toBe(false);
  });

  it("hands a freed slot to the one waiting — a call arriving meanwhile does not jump in too", async () => {
    const run = createLimiter(1);
    let active = 0;
    let peak = 0;
    const job = () => async () => {
      active++;
      peak = Math.max(peak, active);
      await tick();
      active--;
    };
    let end: () => void = () => {};
    const gate = new Promise<void>((resolve) => (end = resolve));
    const first = run(() => gate);
    const queued = run(job());
    // Registered after `run` awaited `gate`, so this runs right after the slot
    // is freed and promised to `queued`, before `queued` gets to start.
    let late: Promise<void> = Promise.resolve();
    void gate.then(() => {
      late = run(job());
    });
    end();
    await first;
    await queued;
    await late;
    expect(peak).toBe(1);
  });

  it("frees the slot when a running call fails", async () => {
    const run = createLimiter(1);
    await expect(run(async () => Promise.reject(new Error("no")))).rejects.toThrow("no");
    expect(await run(async () => "next")).toBe("next");
  });
});

describe("mapWithConcurrency", () => {
  it("maps every item, preserving input order", async () => {
    const out = await mapWithConcurrency([1, 2, 3, 4], 2, async (n) => n * 10);
    expect(out).toEqual([10, 20, 30, 40]);
  });

  it("never runs more than `limit` tasks at once", async () => {
    let active = 0;
    let peak = 0;
    const items = Array.from({ length: 12 }, (_, i) => i);
    await mapWithConcurrency(items, 3, async (i) => {
      active++;
      peak = Math.max(peak, active);
      await new Promise((r) => setTimeout(r, 1));
      active--;
      return i;
    });
    // The whole point of the fix: a folder's worth of files must NOT fan out
    // all at once (that froze the tab); at most `limit` are in flight.
    expect(peak).toBe(3);
  });

  it("returns [] for no items (and runs nothing)", async () => {
    let ran = 0;
    const out = await mapWithConcurrency([], 4, async (x) => {
      ran++;
      return x;
    });
    expect(out).toEqual([]);
    expect(ran).toBe(0);
  });
});
