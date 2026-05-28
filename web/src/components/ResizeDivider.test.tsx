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

  it("reports each pointermove as a signed delta from the previous position", () => {
    const onResize = vi.fn();
    const { getByRole } = render(<ResizeDivider orientation="vertical" onResize={onResize} />);
    const divider = getByRole("separator");

    // setPointerCapture isn't implemented in happy-dom — stub it so the
    // pointerdown handler doesn't throw.
    (divider as unknown as { setPointerCapture: (id: number) => void }).setPointerCapture = vi.fn();
    (divider as unknown as { releasePointerCapture: (id: number) => void }).releasePointerCapture = vi.fn();

    fireEvent(divider, pointer("pointerdown", { clientX: 100 }));
    fireEvent(divider, pointer("pointermove", { clientX: 112 })); // +12
    fireEvent(divider, pointer("pointermove", { clientX: 105 })); // -7
    fireEvent(divider, pointer("pointermove", { clientX: 130 })); // +25
    fireEvent(divider, pointer("pointerup",   { clientX: 130 }));

    expect(onResize).toHaveBeenCalledTimes(3);
    expect(onResize.mock.calls.map((c) => c[0])).toEqual([12, -7, 25]);
  });
});
