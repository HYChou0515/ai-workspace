import { describe, expect, it } from "vitest";

import { absoluteRange, newestNumber, type RangeChoice } from "./chatExportRange";

describe("absoluteRange — the dialog counts from the newest, the server from the oldest", () => {
  const total = 10; // messages 0..9 oldest-first; the newest is #1 in the dialog

  it("all is the whole thread, expressed as no range at all", () => {
    expect(absoluteRange(total, { kind: "all" })).toBeNull();
  });

  it("the latest N are the last N in server order", () => {
    expect(absoluteRange(total, { kind: "latest", n: 3 })).toEqual({ start: 7, end: 10 });
  });

  it("latest N with N past the thread is the whole thread", () => {
    expect(absoluteRange(total, { kind: "latest", n: 50 })).toBeNull();
    expect(absoluteRange(total, { kind: "latest", n: 10 })).toBeNull();
  });

  it("a custom range is given newest-first and comes back half-open, oldest-first", () => {
    // Newest #1 … #4 = server indexes 9 … 6 → [6, 10)
    expect(absoluteRange(total, { kind: "custom", from: 1, to: 4 })).toEqual({ start: 6, end: 10 });
    // Whichever way round the two are picked, the same messages.
    expect(absoluteRange(total, { kind: "custom", from: 4, to: 1 })).toEqual({ start: 6, end: 10 });
    // One message.
    expect(absoluteRange(total, { kind: "custom", from: 10, to: 10 })).toEqual({ start: 0, end: 1 });
  });

  it("a custom range that covers everything is no range", () => {
    expect(absoluteRange(total, { kind: "custom", from: 1, to: 10 })).toBeNull();
  });

  it("a number outside the thread is clamped into it, never sent as an invalid range", () => {
    expect(absoluteRange(total, { kind: "custom", from: 0, to: 99 })).toBeNull();
    expect(absoluteRange(total, { kind: "custom", from: 12, to: 11 })).toEqual({ start: 0, end: 1 });
  });

  it("an empty thread has no range for any choice", () => {
    const choices: RangeChoice[] = [
      { kind: "all" },
      { kind: "latest", n: 3 },
      { kind: "custom", from: 1, to: 2 },
    ];
    for (const c of choices) expect(absoluteRange(0, c)).toBeNull();
  });
});

describe("newestNumber — the label a message carries in the dialog", () => {
  it("is 1 for the newest and `total` for the oldest", () => {
    expect(newestNumber(10, 9)).toBe(1);
    expect(newestNumber(10, 0)).toBe(10);
  });
});
