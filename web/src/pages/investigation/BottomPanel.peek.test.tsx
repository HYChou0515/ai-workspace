// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PanelAction, PanelState } from "../../lib/panelPeek";
import { BottomPanel } from "./WorkspaceShell";

vi.mock("../../hooks/useAgent", () => ({
  useAgent: () => ({ log: { entries: [], streaming: false } }),
}));
vi.mock("./TerminalPane", () => ({ TerminalPane: () => null }));

afterEach(cleanup);

function renderPanel(over: {
  state?: PanelState;
  tab?: "problems" | "output" | "terminal" | "agent_log" | "run_history";
  onTab?: (t: never) => void;
  onPanel?: (a: PanelAction) => void;
}) {
  return render(
    <BottomPanel
      tab={over.tab ?? "agent_log"}
      onTab={(over.onTab ?? vi.fn()) as never}
      investigationId="inv-1"
      showTerminal={true}
      height={200}
      state={over.state ?? "closed"}
      onPanel={over.onPanel ?? vi.fn()}
    />,
  );
}

/** A real double click delivers click(detail 1) → click(detail 2) → dblclick. */
function doubleClick(el: HTMLElement) {
  fireEvent.click(el, { detail: 1 });
  fireEvent.click(el, { detail: 2 });
  fireEvent.doubleClick(el, { detail: 2 });
}

describe("BottomPanel: the strip stays, the body is what collapses", () => {
  it("shows the tab labels but no body while closed", () => {
    renderPanel({ state: "closed" });
    expect(screen.getByRole("button", { name: "Agent log" })).toBeInTheDocument();
    expect(screen.queryByTestId("bottom-body")).not.toBeInTheDocument();
  });

  it("shows the body once peeked", () => {
    renderPanel({ state: "peeked" });
    expect(screen.getByTestId("bottom-body")).toBeInTheDocument();
  });
});

/**
 * Measured in a real browser, not happy-dom: with the body docked in the flex
 * column, the panel grows UPWARD from 32px to its full height, so the first
 * click of a double click shoved the tab row ~168px up and out from under the
 * pointer. The second click landed on the editor instead, `dblclick` never
 * fired, and "double-click to pin" was simply dead — every pin/collapse
 * gesture silently degraded to a peek.
 *
 * A peek therefore FLOATS over the editor (the strip never moves, and a
 * temporary glance does not reflow the work underneath); a pin docks and takes
 * its own space, which is also what makes it worth resizing.
 */
describe("BottomPanel: a peek floats, a pin docks", () => {
  it("keeps the strip in place by floating the peeked body above it", () => {
    renderPanel({ state: "peeked" });
    expect(screen.getByTestId("bottom-body").style.position).toBe("absolute");
  });

  it("leaves the panel at strip height while peeked so the flex row never reflows", () => {
    const { container } = renderPanel({ state: "peeked" });
    expect((container.firstChild as HTMLElement).style.height).toBe("32px");
  });

  it("docks a pinned body in the flow, claiming the panel's full height", () => {
    const { container } = renderPanel({ state: "pinned" });
    expect(screen.getByTestId("bottom-body").style.position).toBe("");
    expect((container.firstChild as HTMLElement).style.height).toBe("200px");
  });
});

describe("BottomPanel: clicking a tab reveals it temporarily", () => {
  it("switches to the clicked tab and peeks the panel open", () => {
    const onTab = vi.fn();
    const onPanel = vi.fn();
    renderPanel({ state: "closed", onTab, onPanel });
    fireEvent.click(screen.getByRole("button", { name: "Terminal" }), { detail: 1 });
    expect(onTab).toHaveBeenCalledWith("terminal");
    expect(onPanel).toHaveBeenCalledWith({ type: "peek" });
  });
});

describe("BottomPanel: double-clicking pins, and pins the visible tab again to collapse", () => {
  it("reports the gesture as same-target when the tab was already the visible one", () => {
    const onPanel = vi.fn();
    renderPanel({ state: "pinned", tab: "agent_log", onPanel });
    doubleClick(screen.getByRole("button", { name: "Agent log" }));
    expect(onPanel).toHaveBeenCalledWith({ type: "pin", sameTarget: true });
  });

  // The regression this guards: the first click of the double click already
  // switched the visible tab, so comparing against the CURRENT tab would call
  // this same-target and collapse the panel instead of switching to Terminal.
  it("reports a different tab as a switch, judged from where the gesture started", () => {
    const onPanel = vi.fn();
    renderPanel({ state: "pinned", tab: "agent_log", onPanel });
    doubleClick(screen.getByRole("button", { name: "Terminal" }));
    expect(onPanel).toHaveBeenCalledWith({ type: "pin", sameTarget: false });
  });
});

describe("BottomPanel: the chevron stays a one-click toggle", () => {
  it("sends a plain toggle", () => {
    const onPanel = vi.fn();
    renderPanel({ state: "closed", onPanel });
    fireEvent.click(screen.getByRole("button", { name: "toggle bottom panel" }));
    expect(onPanel).toHaveBeenCalledWith({ type: "toggle" });
  });
});
