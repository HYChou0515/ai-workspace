// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SteerPlan } from "../api/workflows";
import { SteerConfirmCard } from "./SteerConfirmCard";

afterEach(cleanup);

const PLAN: SteerPlan = {
  instruction: "use the a, b collections and redo the upload",
  rationale: "switch ingest target and re-run only the upload",
  input_edits: [{ path: "collections.json", content: "[{\"id\": \"a\"}]" }],
  invalidate: ["ingest"],
  decided_by: "",
};

describe("SteerConfirmCard", () => {
  it("shows the instruction, rationale and blast radius, and confirms / rejects", () => {
    const onConfirm = vi.fn();
    render(<SteerConfirmCard plan={PLAN} onConfirm={onConfirm} />);

    expect(screen.getByTestId("wf-steer-card")).toBeInTheDocument();
    expect(screen.getByText(/use the a, b collections/)).toBeInTheDocument();
    expect(screen.getByText(/switch ingest target/)).toBeInTheDocument();
    // blast radius: the input file that changes + the step that will re-run
    expect(screen.getByText("collections.json")).toBeInTheDocument();
    expect(screen.getByText("ingest")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("wf-steer-approve"));
    expect(onConfirm).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByTestId("wf-steer-reject"));
    expect(onConfirm).toHaveBeenCalledWith(false);
  });

  it("disables both actions while a confirm is in flight", () => {
    render(<SteerConfirmCard plan={PLAN} onConfirm={vi.fn()} busy />);
    expect(screen.getByTestId("wf-steer-approve")).toBeDisabled();
    expect(screen.getByTestId("wf-steer-reject")).toBeDisabled();
  });

  it("renders an edit-only plan without a re-run list, and vice versa", () => {
    const editOnly: SteerPlan = { ...PLAN, invalidate: [] };
    const { unmount } = render(<SteerConfirmCard plan={editOnly} onConfirm={vi.fn()} />);
    expect(screen.queryByTestId("wf-steer-invalidate")).not.toBeInTheDocument();
    expect(screen.getByTestId("wf-steer-edits")).toBeInTheDocument();
    unmount();

    const invalidateOnly: SteerPlan = { ...PLAN, input_edits: [] };
    render(<SteerConfirmCard plan={invalidateOnly} onConfirm={vi.fn()} />);
    expect(screen.queryByTestId("wf-steer-edits")).not.toBeInTheDocument();
    expect(screen.getByTestId("wf-steer-invalidate")).toBeInTheDocument();
  });
});
