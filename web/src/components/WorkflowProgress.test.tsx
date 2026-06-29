// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PhaseDef, WorkflowRunDTO } from "../api/workflows";
import { WorkflowProgress } from "./WorkflowProgress";

function mkRun(over: Partial<WorkflowRunDTO>): WorkflowRunDTO {
  return {
    run_id: "r1",
    item_id: "topic-hub:1",
    captured_user: "u",
    status: "running",
    current_phase: "",
    phases: [],
    steps: [],
    failures: [],
    started: 1,
    ended: null,
    result: null,
    pending_decision: null,
    ...over,
  };
}

const PHASES: PhaseDef[] = [
  { id: "classify", title: "Classify" },
  { id: "commit", title: "Commit" },
];

function renderProgress(
  over: Partial<WorkflowRunDTO>,
  opts?: { disconnected?: boolean; onStop?: () => void; stopping?: boolean },
) {
  const onStop = opts?.onStop ?? vi.fn();
  render(
    <WorkflowProgress
      run={mkRun(over)}
      declaredPhases={PHASES}
      disconnected={opts?.disconnected ?? false}
      onStop={onStop}
      stopping={opts?.stopping ?? false}
    />,
  );
  return { onStop };
}

beforeEach(() => {
  localStorage.clear();
});
afterEach(() => cleanup());

describe("WorkflowProgress — collapsed bar (#331)", () => {
  it("renders nothing when there is no run yet", () => {
    render(
      <WorkflowProgress run={undefined} declaredPhases={PHASES} disconnected={false} onStop={vi.fn()} />,
    );
    expect(screen.queryByTestId("wf-progress")).toBeNull();
  });

  it("is collapsed by default: the bar shows, the structural detail is hidden", () => {
    renderProgress({
      status: "running",
      current_phase: "classify",
      phases: [{ phase: "classify", status: "running", done: 0, total: 0, failed: 0 }],
    });
    expect(screen.getByTestId("wf-progress-bar")).toBeInTheDocument();
    expect(screen.getByTestId("wf-run-status")).toBeInTheDocument();
    // The detail (#283 viz) is NOT in the DOM until expanded.
    expect(screen.queryByTestId("wf-metrics")).toBeNull();
    expect(screen.queryByTestId("wf-phase-diagram")).toBeNull();
    expect(screen.queryByTestId("wf-step-board")).toBeNull();
    expect(screen.getByTestId("wf-progress-toggle")).toHaveAttribute("aria-expanded", "false");
  });

  it("the collapsed summary names the current phase", () => {
    renderProgress({
      status: "running",
      current_phase: "classify",
      phases: [{ phase: "classify", status: "running", done: 0, total: 0, failed: 0 }],
    });
    expect(screen.getByTestId("wf-progress-summary")).toHaveTextContent("Classify");
  });
});

describe("WorkflowProgress — expand reveals #283 detail (#331)", () => {
  const RUNNING = {
    status: "running" as const,
    current_phase: "classify",
    phases: [{ phase: "classify", status: "running", done: 0, total: 0, failed: 0 }],
    steps: [
      {
        phase: "classify",
        name: "classify_a",
        key: "",
        status: "running",
        attempts: 1,
        reason: "",
        started: 1,
        ended: null,
      },
    ],
  };

  it("expanding shows the phase diagram, metrics and step board", () => {
    renderProgress(RUNNING);
    fireEvent.click(screen.getByTestId("wf-progress-toggle"));
    expect(screen.getByTestId("wf-phase-diagram")).toBeInTheDocument();
    expect(screen.getByTestId("wf-metrics")).toBeInTheDocument();
    expect(screen.getByTestId("wf-step-board")).toBeInTheDocument();
    expect(screen.getByTestId("wf-step-row")).toHaveTextContent("classify_a");
    expect(screen.getByTestId("wf-progress-toggle")).toHaveAttribute("aria-expanded", "true");
  });

  it("remembers the expanded choice across mounts (usePersistentBoolean)", () => {
    localStorage.setItem("wf.progress.expanded", "true");
    renderProgress(RUNNING);
    // expanded straight away, no click needed
    expect(screen.getByTestId("wf-metrics")).toBeInTheDocument();
    expect(screen.getByTestId("wf-step-board")).toBeInTheDocument();
  });

  it("toggles between step board and timeline once expanded (board default)", () => {
    localStorage.setItem("wf.progress.expanded", "true");
    renderProgress({
      status: "done",
      phases: [{ phase: "classify", status: "passed", done: 1, total: 1, failed: 0 }],
      ended: 50,
      steps: [
        {
          phase: "classify",
          name: "classify_a",
          key: "",
          status: "passed",
          attempts: 0,
          reason: "",
          started: 1,
          ended: 50,
        },
      ],
    });
    expect(screen.getByTestId("wf-step-board")).toBeInTheDocument();
    expect(screen.queryByTestId("wf-timeline")).toBeNull();
    fireEvent.click(screen.getByTestId("wf-view-timeline"));
    expect(screen.getByTestId("wf-timeline")).toBeInTheDocument();
    expect(screen.queryByTestId("wf-step-board")).toBeNull();
  });
});

describe("WorkflowProgress — terminal clarity is never buried (#331, #100)", () => {
  it("shows the no-op banner even while collapsed", () => {
    renderProgress({
      status: "done",
      ended: 2,
      phases: [
        { phase: "classify", status: "pending", done: 0, total: 0, failed: 0 },
        { phase: "commit", status: "pending", done: 0, total: 0, failed: 0 },
      ],
      result: { status: "no_collections", message: "這個 Hub 還沒有設定任何知識庫。" },
    });
    const noop = screen.getByTestId("wf-noop");
    expect(noop).toHaveTextContent("未執行任何步驟");
    expect(noop).toHaveTextContent("還沒有設定任何知識庫");
    // internal token must not leak (no-internals rule)
    expect(screen.queryByText(/no_collections/)).toBeNull();
    // proves it's the collapsed bar surfacing it, not the expanded detail
    expect(screen.queryByTestId("wf-metrics")).toBeNull();
  });

  it("shows the human result message on a done run that did work", () => {
    renderProgress({
      status: "done",
      ended: 2,
      phases: [{ phase: "classify", status: "passed", done: 2, total: 2, failed: 0 }],
      result: { status: "approved", message: "已彙整 2 筆資料。" },
    });
    expect(screen.getByTestId("wf-run-message")).toHaveTextContent("已彙整 2 筆資料。");
    expect(screen.queryByTestId("wf-noop")).toBeNull();
  });

  it("lists failures on an errored run", () => {
    renderProgress({
      status: "error",
      ended: 2,
      failures: [{ key: "classify_a", error: "JSON 解析失敗", phase: "classify" }],
      result: { error: "step failed" },
    });
    const failures = screen.getByTestId("wf-failures");
    expect(failures).toHaveTextContent("classify_a");
    expect(failures).toHaveTextContent("JSON 解析失敗");
  });
});

describe("WorkflowProgress — controls (#331)", () => {
  it("warns the connection dropped while the run is live, but not once terminal", () => {
    const { unmount } = render(
      <WorkflowProgress
        run={mkRun({ status: "running" })}
        declaredPhases={PHASES}
        disconnected
        onStop={vi.fn()}
      />,
    );
    expect(screen.getByTestId("wf-disconnected")).toBeInTheDocument();
    unmount();
    render(
      <WorkflowProgress
        run={mkRun({ status: "done", ended: 2 })}
        declaredPhases={PHASES}
        disconnected
        onStop={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("wf-disconnected")).toBeNull();
  });

  it("offers Stop while running and calls onStop", () => {
    const onStop = vi.fn();
    renderProgress({ status: "running" }, { onStop });
    fireEvent.click(screen.getByTestId("wf-stop"));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("hides Stop when terminal or awaiting a human decision", () => {
    const { unmount } = render(
      <WorkflowProgress run={mkRun({ status: "done", ended: 2 })} declaredPhases={PHASES} disconnected={false} onStop={vi.fn()} />,
    );
    expect(screen.queryByTestId("wf-stop")).toBeNull();
    unmount();
    render(
      <WorkflowProgress run={mkRun({ status: "awaiting_human" })} declaredPhases={PHASES} disconnected={false} onStop={vi.fn()} />,
    );
    expect(screen.queryByTestId("wf-stop")).toBeNull();
  });
});
