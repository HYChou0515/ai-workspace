// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentState } from "../../hooks/useAgent";
import { DialogProvider } from "../../components/Dialog";
import { renderWithQuery } from "../../test/queryWrapper";
import { AgentPanel } from "./AgentPanel";

/** happy-dom has no PointerEvent constructor — the shape ResizeDivider reads. */
function pointer(type: string, init: { clientY?: number; pointerId?: number } = {}) {
  const ev = new Event(type, { bubbles: true });
  Object.assign(ev, { clientX: 0, clientY: init.clientY ?? 0, pointerId: init.pointerId ?? 1 });
  return ev;
}

function stubAgent(): AgentState {
  return {
    investigationId: "it1",
    log: { entries: [], streaming: false } as unknown as AgentState["log"],
    connection: { state: "live", receiving: true, error: null, attempts: 0 },
    send: vi.fn(async () => {}),
    mention: vi.fn(async () => {}),
    cancel: vi.fn(),
    undo: vi.fn(async () => {}),
  };
}

function renderPanel() {
  return renderWithQuery(
    <MemoryRouter>
      <DialogProvider>
        <AgentPanel
          investigationId="it1"
          chatId="chat-1"
          agent={stubAgent()}
          picker={[]}
          suggestions={[]}
          attachedPreset=""
          onAttachPreset={() => {}}
          uploadDir="uploads"
        />
      </DialogProvider>
    </MemoryRouter>,
  );
}

afterEach(cleanup);
beforeEach(() => localStorage.clear());

describe("workspace chat composer height", () => {
  it("is dragged from a handle between the feed and the composer", () => {
    // The ask: a real handle on the seam, not the textarea's corner grip —
    // which is a 15px target that only grows the textarea, leaving the rest of
    // the composer (chips, model picker) fixed.
    renderPanel();
    const handle = screen.getByRole("separator", { name: /composer/i });
    // The height belongs to the TYPING AREA, not the whole form: the form also
    // carries the chips row, the usage/token lines and the button row, and
    // pinning its height made that content overflow the panel — seen in a real
    // browser while every unit test was green.
    const box = screen.getByPlaceholderText(/Ask the agent/i);
    const before = Number.parseInt(box.style.height, 10);
    // Guard the guard: with no height at all this is NaN, and NaN + 100 === NaN
    // would let the whole assertion pass against a box that never resizes.
    expect(Number.isFinite(before)).toBe(true);

    // Drag UP (negative delta) — the composer grows, taking room from the feed.
    fireEvent(handle, pointer("pointerdown", { clientY: 400 }));
    fireEvent(handle, pointer("pointermove", { clientY: 300 }));
    fireEvent(handle, pointer("pointerup", { clientY: 300 }));

    expect(Number.parseInt(box.style.height, 10)).toBe(before + 100);
  });

  it("remembers the height, like every other panel size in this app", () => {
    const { unmount } = renderPanel();
    const handle = screen.getByRole("separator", { name: /composer/i });
    fireEvent(handle, pointer("pointerdown", { clientY: 400 }));
    fireEvent(handle, pointer("pointermove", { clientY: 340 }));
    fireEvent(handle, pointer("pointerup", { clientY: 340 }));
    const dragged = Number.parseInt(
      screen.getByPlaceholderText(/Ask the agent/i).style.height,
      10,
    );
    expect(Number.isFinite(dragged)).toBe(true);
    unmount();

    renderPanel();
    expect(Number.parseInt(screen.getByPlaceholderText(/Ask the agent/i).style.height, 10)).toBe(
      dragged,
    );
  });

  it("leaves the composer form itself content-sized, so nothing overflows", () => {
    // The regression this pins: a fixed height on the FORM squeezed the chips,
    // the usage lines and the button row until they spilled past the panel's
    // bottom edge, cutting the send row off screen entirely.
    renderPanel();
    expect(screen.getByTestId("agent-composer").style.height).toBe("");
  });

  it("drops the textarea's own resize grip — the handle replaces it", () => {
    // Two resize affordances on one box is one too many, and the corner grip
    // is the one that resizes only half the thing.
    renderPanel();
    const box = screen.getByPlaceholderText(/Ask the agent/i);
    expect(box.style.resize === "" || box.style.resize === "none").toBe(true);
  });
});
