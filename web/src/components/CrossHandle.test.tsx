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
  it("positions absolutely at (leftPct, topPct) of its parent", () => {
    const { getByRole } = render(
      <div style={{ position: "relative" }}>
        <CrossHandle leftPct={0.5} topPct={0.5} onResize={vi.fn()} />
      </div>,
    );
    const handle = getByRole("button", { name: /resize panes/i });
    expect(handle.style.left).toBe("50%");
    expect(handle.style.top).toBe("50%");
    // hit area must be grabbable (≥ 12 px)
    expect(Number.parseInt(handle.style.width, 10)).toBeGreaterThanOrEqual(12);
    expect(Number.parseInt(handle.style.height, 10)).toBeGreaterThanOrEqual(12);
  });

  it("reports (dx, dy) as deltas from the drag-start cursor (anchored)", () => {
    const onResizeStart = vi.fn();
    const onResize = vi.fn();
    const onResizeEnd = vi.fn();
    const { getByRole } = render(
      <div style={{ position: "relative" }}>
        <CrossHandle
          leftPct={0.5}
          topPct={0.5}
          onResizeStart={onResizeStart}
          onResize={onResize}
          onResizeEnd={onResizeEnd}
        />
      </div>,
    );
    const handle = getByRole("button", { name: /resize panes/i });
    (handle as unknown as { setPointerCapture: (id: number) => void }).setPointerCapture = vi.fn();
    (handle as unknown as { releasePointerCapture: (id: number) => void }).releasePointerCapture = vi.fn();

    fireEvent(handle, pointer("pointerdown", { clientX: 100, clientY: 200 }));
    fireEvent(handle, pointer("pointermove", { clientX: 130, clientY: 210 })); // (+30, +10)
    fireEvent(handle, pointer("pointermove", { clientX: 90, clientY: 250 }));  // (-10, +50)
    fireEvent(handle, pointer("pointerup",   { clientX: 90, clientY: 250 }));

    expect(onResizeStart).toHaveBeenCalledTimes(1);
    expect(onResize.mock.calls).toEqual([[30, 10], [-10, 50]]);
    expect(onResizeEnd).toHaveBeenCalledTimes(1);
  });
});
