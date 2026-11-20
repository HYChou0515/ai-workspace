import { describe, expect, it } from "vitest";

import { FEED_HEAD, FEED_STEP, FEED_TAIL, feedWindow } from "./collapsedFeed";

describe("feedWindow (docs/plan-chat-collapse-middle.md D1/D5)", () => {
  it("defaults to the first 100 and the newest 1000", () => {
    expect([FEED_HEAD, FEED_TAIL, FEED_STEP]).toEqual([100, 1000, 1000]);
  });

  it("draws everything when the head and tail meet", () => {
    expect(feedWindow(1100, 100, 1000)).toEqual({ headEnd: 1100, tailStart: 1100, hidden: 0 });
    expect(feedWindow(5, 100, 1000)).toEqual({ headEnd: 5, tailStart: 5, hidden: 0 });
    expect(feedWindow(0, 100, 1000)).toEqual({ headEnd: 0, tailStart: 0, hidden: 0 });
  });

  it("hides what lies between the head and the tail", () => {
    expect(feedWindow(1101, 100, 1000)).toEqual({ headEnd: 100, tailStart: 101, hidden: 1 });
    expect(feedWindow(5000, 100, 1000)).toEqual({ headEnd: 100, tailStart: 4000, hidden: 3900 });
  });

  it("counts the tail from the end, so a new message pushes its oldest row into the gap", () => {
    expect(feedWindow(5001, 100, 1000)).toEqual({ headEnd: 100, tailStart: 4001, hidden: 3901 });
  });

  it("grows each side by what was expanded", () => {
    expect(feedWindow(5000, 1100, 1000)).toEqual({ headEnd: 1100, tailStart: 4000, hidden: 2900 });
    expect(feedWindow(5000, 100, 2000)).toEqual({ headEnd: 100, tailStart: 3000, hidden: 2900 });
  });
});
