// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkflowRunDTO } from "../api/workflows";
import { WorkflowRunPanel } from "./WorkflowRunPanel";

const run = vi.hoisted(() => ({ current: null as WorkflowRunDTO | null }));

vi.mock("../hooks/useWorkflow", () => ({
  useRun: () => ({ data: run.current }),
  useCancelRun: () => ({ mutate: vi.fn(), isPending: false }),
  useDecide: () => ({ mutate: vi.fn(), isPending: false }),
}));

afterEach(cleanup);

function mkRun(over: Partial<WorkflowRunDTO>): WorkflowRunDTO {
  return {
    run_id: "r1",
    item_id: "topic-hub:1",
    captured_user: "u",
    status: "done",
    current_phase: "",
    phases: [],
    failures: [],
    started: 1,
    ended: 2,
    result: null,
    pending_decision: null,
    ...over,
  };
}

const PHASES = [
  { id: "classify", title: "Classify" },
  { id: "commit", title: "Commit" },
];

describe("WorkflowRunPanel — no-op / terminal clarity (#100)", () => {
  it("shows the human-readable result message, not the raw status token", () => {
    run.current = mkRun({
      status: "done",
      phases: [
        { phase: "classify", status: "pending", done: 0, total: 0, failed: 0 },
        { phase: "commit", status: "pending", done: 0, total: 0, failed: 0 },
      ],
      result: { status: "no_collections", message: "這個 Hub 還沒有設定任何知識庫。" },
    });
    render(<WorkflowRunPanel slug="topic-hub" itemId="topic-hub:1" runId="r1" declaredPhases={PHASES} />);
    expect(screen.getByText(/還沒有設定任何知識庫/)).toBeInTheDocument();
    // The internal token must not leak into the UI (no-internals rule).
    expect(screen.queryByText(/no_collections/)).not.toBeInTheDocument();
  });

  it("flags a done run that executed no steps", () => {
    run.current = mkRun({
      status: "done",
      phases: [
        { phase: "classify", status: "pending", done: 0, total: 0, failed: 0 },
        { phase: "commit", status: "pending", done: 0, total: 0, failed: 0 },
      ],
      result: { status: "no_collections", message: "原因…" },
    });
    render(<WorkflowRunPanel slug="topic-hub" itemId="topic-hub:1" runId="r1" declaredPhases={PHASES} />);
    expect(screen.getByTestId("wf-noop")).toHaveTextContent("未執行任何步驟");
  });

  it("does NOT flag a done run that actually did work", () => {
    run.current = mkRun({
      status: "done",
      phases: [
        { phase: "classify", status: "passed", done: 2, total: 2, failed: 0 },
        { phase: "commit", status: "passed", done: 2, total: 2, failed: 0 },
      ],
      result: { status: "approved", ingested: 2, cards: 1 },
    });
    render(<WorkflowRunPanel slug="topic-hub" itemId="topic-hub:1" runId="r1" declaredPhases={PHASES} />);
    expect(screen.queryByTestId("wf-noop")).not.toBeInTheDocument();
  });
});
