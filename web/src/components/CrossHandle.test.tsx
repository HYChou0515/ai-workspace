// @vitest-environment happy-dom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CrossHandle } from "./CrossHandle";

afterEach(cleanup);

function pointer(
  type: string,
  init: { clientX?: number; clientY?: number; pointerId?: number } = {},
) {
  const ev = new Event(type, { bubbles: true });
  Object.assign(ev, {
    clientX: init.clientX ?? 0,
    clientY: init.clientY ?? 0,
    pointerId: init.pointerId ?? 1,
  });
  return ev;
}

describe("<CrossHandle />", () => {
  it("renders absolutely positioned at the given (left, top) point", () => {
    const { getByRole } = render(
      <CrossHandle left="40%" top="30%" onResize={vi.fn()} />,
    );
    const h = getByRole("separator");
    expect(h.style.position).toBe("absolute");
    expect(h.style.left).toBe("40%");
    expect(h.style.top).toBe("30%");
    // square hit area, ≥10px on each side so it's grabbable
    expect(Number.parseInt(h.style.width, 10)).toBeGreaterThanOrEqual(10);
    expect(Number.parseInt(h.style.height, 10)).toBeGreaterThanOrEqual(10);
  });

  it("reports (dx, dy) absolute deltas from drag start; fires start/end hooks", () => {
    const onResizeStart = vi.fn();
    const onResize = vi.fn();
    const onResizeEnd = vi.fn();
    const { getByRole } = render(
      <CrossHandle
        left="50%"
        top="50%"
        onResizeStart={onResizeStart}
        onResize={onResize}
        onResizeEnd={onResizeEnd}
      />,
    );
    const h = getByRole("separator");
    (h as unknown as { setPointerCapture: (id: number) => void }).setPointerCapture = vi.fn();
    (h as unknown as { releasePointerCapture: (id: number) => void }).releasePointerCapture = vi.fn();

    fireEvent(h, pointer("pointerdown", { clientX: 100, clientY: 200 }));
    fireEvent(h, pointer("pointermove", { clientX: 120, clientY: 215 })); // +20, +15
    fireEvent(h, pointer("pointermove", { clientX: 130, clientY: 190 })); // +30, -10
    fireEvent(h, pointer("pointerup", { clientX: 130, clientY: 190 }));

    expect(onResizeStart).toHaveBeenCalledTimes(1);
    expect(onResize.mock.calls).toEqual([
      [20, 15],
      [30, -10],
    ]);
    expect(onResizeEnd).toHaveBeenCalledTimes(1);
  });
});
