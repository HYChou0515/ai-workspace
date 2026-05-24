// @vitest-environment happy-dom
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useOnTurnEnd } from "./useOnTurnEnd";

describe("useOnTurnEnd", () => {
  it("fires when streaming transitions true → false (turn finished)", () => {
    const cb = vi.fn();
    const { rerender } = renderHook(({ s }) => useOnTurnEnd(s, cb), {
      initialProps: { s: true },
    });
    expect(cb).not.toHaveBeenCalled(); // still streaming
    rerender({ s: false });
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("does not fire on the initial render when already idle", () => {
    const cb = vi.fn();
    renderHook(({ s }) => useOnTurnEnd(s, cb), { initialProps: { s: false } });
    expect(cb).not.toHaveBeenCalled();
  });

  it("does not fire when a new turn starts (false → true)", () => {
    const cb = vi.fn();
    const { rerender } = renderHook(({ s }) => useOnTurnEnd(s, cb), {
      initialProps: { s: false },
    });
    rerender({ s: true });
    expect(cb).not.toHaveBeenCalled();
  });

  it("fires once per turn across multiple turns", () => {
    const cb = vi.fn();
    const { rerender } = renderHook(({ s }) => useOnTurnEnd(s, cb), {
      initialProps: { s: false },
    });
    rerender({ s: true }); // turn 1 starts
    rerender({ s: false }); // turn 1 ends → fire
    rerender({ s: true }); // turn 2 starts
    rerender({ s: false }); // turn 2 ends → fire
    expect(cb).toHaveBeenCalledTimes(2);
  });
});
