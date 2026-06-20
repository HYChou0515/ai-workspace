// @vitest-environment happy-dom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResizeDivider } from "./ResizeDivider";

afterEach(cleanup);

/** happy-dom doesn't implement PointerEvent constructor — fall back to a
 * generic Event with the bits ResizeDivider reads (clientX/Y + pointerId). */
function pointer(type: string, init: { clientX?: number; clientY?: number; pointerId?: number } = {}) {
  const ev = new Event(type, { bubbles: true });
  Object.assign(ev, { clientX: init.clientX ?? 0, clientY: init.clientY ?? 0, pointerId: init.pointerId ?? 1 });
  return ev;
}

describe("<ResizeDivider />", () => {
  it("exposes a hit area wide enough to grab comfortably (vertical)", () => {
    const { getByRole } = render(<ResizeDivider orientation="vertical" onResize={vi.fn()} />);
    const divider = getByRole("separator");
    // 5px (the old size) is too thin to grab — bump the floor to 10px so the
    // user has a real target. The exact number is a parity-with-design pick
    // (12px is what we ship), but we lock in ≥10 to prevent regressions.
    expect(Number.parseInt(divider.style.width, 10)).toBeGreaterThanOrEqual(10);
  });

  it("exposes a hit area wide enough to grab comfortably (horizontal)", () => {
    const { getByRole } = render(<ResizeDivider orientation="horizontal" onResize={vi.fn()} />);
    const divider = getByRole("separator");
    expect(Number.parseInt(divider.style.height, 10)).toBeGreaterThanOrEqual(10);
  });

  it("renders a visible line that stretches along the divider's main axis", () => {
    // Regression: the first attempt at a layered hit area used flex
    // alignSelf:stretch on the inner line. That made VERTICAL lines tall
    // and visible, but HORIZONTAL ones collapsed to 0×1 (invisible) — the
    // bottom-panel divider became un-grabbable because the user couldn't
    // see it.
    const { getByRole: getV, unmount: u1 } = render(
      <ResizeDivider orientation="vertical" onResize={vi.fn()} />,
    );
    const vLine = getV("separator").querySelector("[aria-hidden]") as HTMLElement | null;
    expect(vLine).not.toBeNull();
    // Vertical: line must fill the height (top:0 + bottom:0 or height:100%).
    expect(vLine?.style.top).toBe("0px");
    expect(vLine?.style.bottom).toBe("0px");
    u1();

    const { getByRole: getH } = render(
      <ResizeDivider orientation="horizontal" onResize={vi.fn()} />,
    );
    const hLine = getH("separator").querySelector("[aria-hidden]") as HTMLElement | null;
    expect(hLine).not.toBeNull();
    // Horizontal: line must fill the width.
    expect(hLine?.style.left).toBe("0px");
    expect(hLine?.style.right).toBe("0px");
  });

  it("reports each pointermove as an absolute delta from the DRAG START position", () => {
    // Anchored to drag-start (not last event) so:
    //  - coalesced pointer events at high speed don't accumulate error
    //  - the value tracks the cursor 1:1 even after a clamp (overshoot
    //    + come back gives back the same value, not a transient).
    const onResizeStart = vi.fn();
    const onResize = vi.fn();
    const onResizeEnd = vi.fn();
    const { getByRole } = render(
      <ResizeDivider
        orientation="vertical"
        onResizeStart={onResizeStart}
        onResize={onResize}
        onResizeEnd={onResizeEnd}
      />,
    );
    const divider = getByRole("separator");
    (divider as unknown as { setPointerCapture: (id: number) => void }).setPointerCapture = vi.fn();
    (divider as unknown as { releasePointerCapture: (id: number) => void }).releasePointerCapture = vi.fn();

    fireEvent(divider, pointer("pointerdown", { clientX: 100 }));
    fireEvent(divider, pointer("pointermove", { clientX: 112 })); // +12 from start
    fireEvent(divider, pointer("pointermove", { clientX: 105 })); // +5 from start
    fireEvent(divider, pointer("pointermove", { clientX: 130 })); // +30 from start
    fireEvent(divider, pointer("pointerup",   { clientX: 130 }));

    expect(onResizeStart).toHaveBeenCalledTimes(1);
    expect(onResize.mock.calls.map((c) => c[0])).toEqual([12, 5, 30]);
    expect(onResizeEnd).toHaveBeenCalledTimes(1);
  });

  it("renders a collapse chevron only when `collapse` is supplied", () => {
    const { queryByRole, rerender } = render(
      <ResizeDivider orientation="vertical" onResize={vi.fn()} />,
    );
    expect(queryByRole("button")).toBeNull();
    rerender(
      <ResizeDivider
        orientation="vertical"
        onResize={vi.fn()}
        collapse={{ label: "Collapse workspace", icon: "chev_l", onToggle: vi.fn() }}
      />,
    );
    expect(queryByRole("button", { name: "Collapse workspace" })).not.toBeNull();
  });

  it("clicking the chevron toggles, and grabbing it does NOT start a resize", () => {
    const onToggle = vi.fn();
    const onResizeStart = vi.fn();
    const { getByRole } = render(
      <ResizeDivider
        orientation="vertical"
        onResize={vi.fn()}
        onResizeStart={onResizeStart}
        collapse={{ label: "Collapse workspace", icon: "chev_l", onToggle }}
      />,
    );
    const btn = getByRole("button", { name: "Collapse workspace" });
    // pointerdown on the chevron is stopped before it reaches the divider.
    fireEvent(btn, pointer("pointerdown", { clientX: 100 }));
    expect(onResizeStart).not.toHaveBeenCalled();
    fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
