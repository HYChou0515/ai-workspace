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

  it("shows a grip at REST, not only on hover (nobody hovers what looks inert)", () => {
    // The whole finding: an affordance that appears only on hover asks the
    // person to discover it by accident. Guidance for drag targets that are not
    // obviously draggable is a PERSISTENT handle — hover then confirms it.
    // https://smart-interface-design-patterns.com/articles/drag-and-drop-ux/
    const { getByRole } = render(<ResizeDivider orientation="horizontal" onResize={vi.fn()} />);
    const grip = getByRole("separator").querySelector("[data-grip]") as HTMLElement | null;
    expect(grip).not.toBeNull();
    // Visible without any pointer having gone near it.
    expect(grip!.style.opacity === "" || Number(grip!.style.opacity)).toBeTruthy();
  });

  it("meets the 24px minimum target size (WCAG 2.2 SC 2.5.8)", () => {
    // 12px was under the AA floor for pointer targets:
    // https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html
    const { getByRole: getH } = render(
      <ResizeDivider orientation="horizontal" onResize={vi.fn()} />,
    );
    expect(Number.parseInt(getH("separator").style.height, 10)).toBeGreaterThanOrEqual(24);
    cleanup();
    const { getByRole: getV } = render(<ResizeDivider orientation="vertical" onResize={vi.fn()} />);
    expect(Number.parseInt(getV("separator").style.width, 10)).toBeGreaterThanOrEqual(24);
  });

  it("is operable from the keyboard, as the splitter pattern requires", () => {
    // role="separator" that moves a pane must be focusable and driven by the
    // arrow keys — https://www.w3.org/WAI/ARIA/apg/patterns/windowsplitter/
    const onResize = vi.fn();
    const onResizeStart = vi.fn();
    const { getByRole } = render(
      <ResizeDivider
        orientation="horizontal"
        onResize={onResize}
        onResizeStart={onResizeStart}
      />,
    );
    const sep = getByRole("separator");
    expect(sep.tabIndex).toBe(0);

    fireEvent.keyDown(sep, { key: "ArrowUp" });
    // Anchored the same way a drag is, then moved by one step in the negative
    // direction (up = smaller y = negative delta, exactly like a pointer drag).
    expect(onResizeStart).toHaveBeenCalled();
    expect(onResize).toHaveBeenCalledWith(-8);

    fireEvent.keyDown(sep, { key: "ArrowDown" });
    expect(onResize).toHaveBeenLastCalledWith(8);
  });

  it("publishes its position for assistive tech when the parent knows it", () => {
    const { getByRole } = render(
      <ResizeDivider
        orientation="horizontal"
        onResize={vi.fn()}
        value={120}
        min={40}
        max={400}
      />,
    );
    const sep = getByRole("separator");
    expect(sep.getAttribute("aria-valuenow")).toBe("120");
    expect(sep.getAttribute("aria-valuemin")).toBe("40");
    expect(sep.getAttribute("aria-valuemax")).toBe("400");
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

});
