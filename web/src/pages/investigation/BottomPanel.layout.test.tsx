// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BottomPanel } from "./WorkspaceShell";

// The panel body subscribes to the live agent log; these layout tests only care
// about the tab row, so stub the stream out.
vi.mock("../../hooks/useAgent", () => ({
  useAgent: () => ({ log: { entries: [], streaming: false } }),
}));
vi.mock("./TerminalPane", () => ({ TerminalPane: () => null }));

afterEach(cleanup);

function renderPanel() {
  return render(
    <BottomPanel
      tab="agent_log"
      onTab={vi.fn()}
      investigationId="inv-1"
      showTerminal={true}
      height={200}
      open={true}
      onToggle={vi.fn()}
    />,
  );
}

/**
 * Measured in a real browser: the five tab labels (Problems / Output /
 * Terminal / Agent log / Run history) sit in a `height: 32` flex row with no
 * wrap control and no scroll container. Below ~1024px the two-word labels
 * wrapped to a second line *inside* the 32px row and were clipped mid-glyph;
 * at 390px the trailing collapse chevron was pushed past the viewport edge,
 * which is what put a horizontal scrollbar on the whole document.
 *
 * A tab strip that cannot fit scrolls — the same treatment the editor tab
 * strip already gets.
 */
describe("BottomPanel tab row survives a narrow panel (#fe-responsive)", () => {
  it("scrolls the tabs horizontally instead of letting them wrap or overflow", () => {
    renderPanel();
    const strip = screen.getByTestId("bottom-tabs") as HTMLElement;
    expect(strip.style.overflowX).toBe("auto");
    expect(strip.style.minWidth).toBe("0");
  });

  it("keeps every tab label on one line so it cannot wrap inside the 32px row", () => {
    renderPanel();
    for (const label of ["Problems", "Output", "Terminal", "Agent log", "Run history"]) {
      const tab = screen.getByRole("button", { name: label });
      expect(tab.style.whiteSpace).toBe("nowrap");
      expect(tab.style.flexShrink).toBe("0");
    }
  });

  it("pins the collapse toggle outside the scroll area so it stays reachable", () => {
    renderPanel();
    const toggle = screen.getByRole("button", { name: "toggle bottom panel" });
    expect(toggle.style.flexShrink).toBe("0");
    // It must NOT be inside the scrolling tab strip, or it scrolls away with them.
    expect(screen.getByTestId("bottom-tabs").contains(toggle)).toBe(false);
  });
});
