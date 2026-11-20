// @vitest-environment happy-dom
/**
 * The bottom panel's transcripts fold a very long chat's middle the way the chat
 * does (docs/plan-chat-collapse-middle.md): a pinned "Agent log" tab drew every
 * entry in full, so opening a long chat with it pinned was as slow as before.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BottomPanel } from "./WorkspaceShell";

const N = 1500;
const entries = Array.from({ length: N }, (_, i) => ({
  kind: "tool_call",
  call: { id: `c${i}`, name: `tool${i}`, args: {}, status: "done", output: `out ${i}` },
}));

vi.mock("../../hooks/useAgent", () => ({
  useAgent: () => ({ investigationId: "inv-1", log: { entries, streaming: false } }),
}));
vi.mock("./TerminalPane", () => ({ TerminalPane: () => null }));

afterEach(cleanup);

function renderTab(tab: "output" | "agent_log" | "run_history") {
  return render(
    <BottomPanel
      tab={tab}
      onTab={vi.fn() as never}
      investigationId="inv-1"
      showTerminal={true}
      height={200}
      state="pinned"
      onPanel={vi.fn()}
    />,
  );
}

describe("the bottom panel folds the middle of a very long log", () => {
  it.each(["agent_log", "output", "run_history"] as const)("%s draws the first 100 and newest 1000", (tab) => {
    renderTab(tab);
    const body = screen.getByTestId("bottom-body");
    expect(body.textContent).toContain("tool99");
    expect(body.textContent).not.toContain("tool100(");
    expect(body.textContent).not.toMatch(/tool100\b/);
    expect(body.textContent).toContain("tool500");
    expect(screen.getByText("中間還有 400 則未顯示")).toBeInTheDocument();
  });
});

describe("the output tab counts only calls that have output", () => {
  it("folds 1500 calls with output, not the silent ones between them", () => {
    // Interleave 1500 silent calls (no output yet): they draw nothing, so they
    // must not use up the first 100 / newest 1000 or the hidden count.
    const real = entries.slice();
    entries.length = 0;
    for (const e of real) {
      entries.push(e, { kind: "tool_call", call: { id: `s${e.call.id}`, name: "silent", args: {}, status: "running" } } as never);
    }
    try {
      renderTab("output");
      expect(screen.getByText("中間還有 400 則未顯示")).toBeInTheDocument();
      expect(screen.getByTestId("bottom-body").textContent).toContain("tool99");
    } finally {
      entries.length = 0;
      entries.push(...real);
    }
  });
});
