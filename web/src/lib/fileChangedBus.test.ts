import { describe, expect, it, vi } from "vitest";

import { publishFileChanged, subscribeFileChanged } from "./fileChangedBus";

describe("fileChangedBus", () => {
  it("tells a subscriber which file changed", () => {
    const seen = vi.fn();
    const off = subscribeFileChanged("item1", seen);

    publishFileChanged("item1", "/notes.md");

    expect(seen).toHaveBeenCalledWith("/notes.md");
    off();
  });

  it("keeps items apart, so one workspace's edit is not another's", () => {
    const seen = vi.fn();
    const off = subscribeFileChanged("item1", seen);

    publishFileChanged("item2", "/notes.md");

    expect(seen).not.toHaveBeenCalled();
    off();
  });

  it("stops delivering once unsubscribed", () => {
    const seen = vi.fn();
    subscribeFileChanged("item1", seen)();

    publishFileChanged("item1", "/notes.md");

    expect(seen).not.toHaveBeenCalled();
  });

  it("delivers to every subscriber, since a workspace can show two of them", () => {
    const a = vi.fn();
    const b = vi.fn();
    const offA = subscribeFileChanged("item1", a);
    const offB = subscribeFileChanged("item1", b);

    publishFileChanged("item1", "/notes.md");

    expect(a).toHaveBeenCalledOnce();
    expect(b).toHaveBeenCalledOnce();
    offA();
    offB();
  });

  it("survives a subscriber that throws, so one bad listener is not a mute button", () => {
    const boom = vi.fn(() => {
      throw new Error("nope");
    });
    const fine = vi.fn();
    const offA = subscribeFileChanged("item1", boom);
    const offB = subscribeFileChanged("item1", fine);

    expect(() => publishFileChanged("item1", "/notes.md")).not.toThrow();
    expect(fine).toHaveBeenCalledOnce();
    offA();
    offB();
  });
});
