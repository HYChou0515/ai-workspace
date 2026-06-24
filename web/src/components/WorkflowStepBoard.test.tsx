// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PhaseNode, StepStateDTO } from "../api/workflows";
import { WorkflowStepBoard } from "./WorkflowStepBoard";

afterEach(cleanup);

function node(over: Partial<PhaseNode> = {}): PhaseNode {
  return {
    id: "commit",
    title: "Commit",
    status: "running",
    done: 0,
    total: 0,
    failed: 0,
    current: true,
    ...over,
  };
}

function step(over: Partial<StepStateDTO> = {}): StepStateDTO {
  return {
    phase: "commit",
    name: "ingest",
    key: "",
    status: "running",
    attempts: 1,
    reason: "",
    started: null,
    ended: null,
    ...over,
  };
}

const T = 1_700_000_000_000;

describe("WorkflowStepBoard (#178)", () => {
  it("renders nothing when there are no steps and no phase progress", () => {
    const { container } = render(<WorkflowStepBoard nodes={[node()]} steps={[]} />);
    expect(container.querySelector('[data-testid="wf-step-board"]')).toBeNull();
  });

  it("lists a step with its status and name", () => {
    render(<WorkflowStepBoard nodes={[node()]} steps={[step({ name: "compile", status: "running" })]} />);
    const row = screen.getByTestId("wf-step-row");
    expect(row).toHaveAttribute("data-status", "running");
    expect(row).toHaveTextContent("compile");
  });

  it("ticks a running step's server-side elapsed (alive-vs-dead signal)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(T);
    try {
      render(<WorkflowStepBoard nodes={[node()]} steps={[step({ started: T - 3000 })]} />);
      expect(screen.getByTestId("wf-step-elapsed")).toHaveTextContent("0:03");
      act(() => void vi.advanceTimersByTime(2000));
      expect(screen.getByTestId("wf-step-elapsed")).toHaveTextContent("0:05");
    } finally {
      vi.useRealTimers();
    }
  });

  it("freezes a finished step's duration (no ticking)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(T);
    try {
      render(
        <WorkflowStepBoard
          nodes={[node({ status: "passed", done: 1 })]}
          steps={[step({ status: "passed", started: T - 8000, ended: T - 1000 })]}
        />,
      );
      expect(screen.getByTestId("wf-step-elapsed")).toHaveTextContent("0:07");
      act(() => void vi.advanceTimersByTime(60_000));
      expect(screen.getByTestId("wf-step-elapsed")).toHaveTextContent("0:07"); // frozen
    } finally {
      vi.useRealTimers();
    }
  });

  it("surfaces a failed step's reason", () => {
    render(
      <WorkflowStepBoard
        nodes={[node({ status: "failed", failed: 1 })]}
        steps={[step({ status: "failed", reason: "bad collection", started: T, ended: T + 1 })]}
      />,
    );
    expect(screen.getByTestId("wf-step-reason")).toHaveTextContent("bad collection");
  });

  it("shows the retry count when a step took more than one attempt", () => {
    render(
      <WorkflowStepBoard nodes={[node()]} steps={[step({ status: "passed", attempts: 3, started: T, ended: T + 1 })]} />,
    );
    expect(screen.getByTestId("wf-step-attempts")).toHaveTextContent("×3");
  });

  it("shows a collapsed loop as the phase counter even with no individual rows", () => {
    // 100-file commit: elements are collapsed server-side into done/total.
    render(<WorkflowStepBoard nodes={[node({ done: 87, total: 100, failed: 1 })]} steps={[]} />);
    expect(screen.getByTestId("wf-step-board")).toHaveTextContent("87 / 100 · 1 failed");
    expect(screen.queryByTestId("wf-step-row")).toBeNull();
  });
});
