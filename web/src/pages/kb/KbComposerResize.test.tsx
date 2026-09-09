// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render as rtlRender, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { mockKbApi } from "../../api/kbMock";
import { QueryWrap } from "../../test/queryWrapper";
import { KbChatPanel } from "./KbChatPanel";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

/** happy-dom has no PointerEvent constructor — the shape ResizeDivider reads. */
function pointer(type: string, init: { clientY?: number; pointerId?: number } = {}) {
  const ev = new Event(type, { bubbles: true });
  Object.assign(ev, { clientX: 0, clientY: init.clientY ?? 0, pointerId: init.pointerId ?? 1 });
  return ev;
}

afterEach(cleanup);
beforeEach(() => localStorage.clear());

describe("kb chat composer height", () => {
  it("is dragged from a handle on the feed/composer seam", async () => {
    render(<KbChatPanel chatId={null} client={mockKbApi} />);
    const handle = await screen.findByRole("separator", { name: /composer/i });
    // The TYPING AREA carries the height — the composer block also holds the
    // attachment chip and the button row, and pinning the block squeezed them.
    const box = screen.getByPlaceholderText(/knowledge base|知識庫/i);
    const before = Number.parseInt(box.style.height, 10);
    // NaN + 80 === NaN would make this assertion pass against a box that never
    // resizes, so require a real number first.
    expect(Number.isFinite(before)).toBe(true);

    fireEvent(handle, pointer("pointerdown", { clientY: 500 }));
    fireEvent(handle, pointer("pointermove", { clientY: 420 }));
    fireEvent(handle, pointer("pointerup", { clientY: 420 }));

    expect(Number.parseInt(box.style.height, 10)).toBe(before + 80);
    expect(screen.getByTestId("kb-composer").style.height).toBe(""); // block stays content-sized
  });

  it("keeps its own height, separate from the workspace chat's", async () => {
    // Two different surfaces, two different natural sizes — one shared key
    // would make resizing the KB drawer silently reshape the workspace chat.
    render(<KbChatPanel chatId={null} client={mockKbApi} />);
    const handle = await screen.findByRole("separator", { name: /composer/i });
    fireEvent(handle, pointer("pointerdown", { clientY: 500 }));
    fireEvent(handle, pointer("pointermove", { clientY: 450 }));
    fireEvent(handle, pointer("pointerup", { clientY: 450 }));

    const keys = Object.keys(localStorage).filter((k) => k.includes("composer"));
    expect(keys).toHaveLength(1);
    expect(keys[0]).not.toBe("chat:composerHeight"); // the workspace chat's key
  });
});
