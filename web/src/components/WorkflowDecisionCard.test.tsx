// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PendingDecision } from "../api/workflows";
import { WorkflowDecisionCard } from "./WorkflowDecisionCard";

afterEach(cleanup);

const decision = (over: Partial<PendingDecision> = {}): PendingDecision => ({
  phase: "review",
  title: "Approve filing these?",
  summary: "file-a → kb-docs",
  allow: ["approve", "reject", "revise"],
  decided_by: "",
  ...over,
});

describe("WorkflowDecisionCard", () => {
  it("shows the title + summary to review", () => {
    render(<WorkflowDecisionCard decision={decision()} onDecide={() => {}} />);
    expect(screen.getByText("Approve filing these?")).toBeInTheDocument();
    expect(screen.getByTestId("wf-decision-summary")).toHaveTextContent("file-a → kb-docs");
  });

  it("approve / reject post the bare choice", () => {
    const onDecide = vi.fn();
    render(<WorkflowDecisionCard decision={decision()} onDecide={onDecide} />);
    fireEvent.click(screen.getByText("Approve"));
    expect(onDecide).toHaveBeenCalledWith("approve", undefined);
    fireEvent.click(screen.getByText("Reject"));
    expect(onDecide).toHaveBeenCalledWith("reject", undefined);
  });

  it("revise reveals an input, then submits the note", () => {
    const onDecide = vi.fn();
    render(<WorkflowDecisionCard decision={decision()} onDecide={onDecide} />);
    // first click reveals the input without deciding
    fireEvent.click(screen.getByText("Revise"));
    expect(onDecide).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId("wf-revise-input"), { target: { value: "use kb-logs" } });
    fireEvent.click(screen.getByText("Revise"));
    expect(onDecide).toHaveBeenCalledWith("revise", "use kb-logs");
  });

  it("defaults to approve/reject when the gate declares no actions", () => {
    render(<WorkflowDecisionCard decision={decision({ allow: [] })} onDecide={() => {}} />);
    expect(screen.getByText("Approve")).toBeInTheDocument();
    expect(screen.getByText("Reject")).toBeInTheDocument();
  });
});
