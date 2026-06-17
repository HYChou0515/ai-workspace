// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkflowManifestDTO } from "../api/workflows";
import { RunWorkflowPicker } from "./RunWorkflowPicker";

afterEach(cleanup);

const WORKFLOWS: WorkflowManifestDTO[] = [
  {
    id: "memory",
    title: "Digest uploads into memory",
    tag: "batch",
    description: "Digest each uploaded file into the Hub's memory.",
    hint: "Drop files into inputs/.",
    phases: [],
    input_json: "x",
  },
  {
    id: "consolidate",
    title: "Consolidate memory",
    tag: "single",
    description: "Re-read current memory and rewrite the memory files.",
    hint: "No inputs needed.",
    phases: [],
    input_json: "x",
  },
];

describe("RunWorkflowPicker", () => {
  it("renders nothing when there are no workflows", () => {
    const { container } = render(
      <RunWorkflowPicker workflows={[]} onLaunch={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists each workflow as a rich card (title · tag · description · hint)", () => {
    render(<RunWorkflowPicker workflows={WORKFLOWS} onLaunch={() => {}} />);
    fireEvent.click(screen.getByTestId("run-workflow-button"));
    expect(screen.getByText("Workflows on this profile")).toBeInTheDocument();
    // titles
    expect(screen.getByText("Digest uploads into memory")).toBeInTheDocument();
    expect(screen.getByText("Consolidate memory")).toBeInTheDocument();
    // tag pills
    expect(screen.getByText("batch")).toBeInTheDocument();
    expect(screen.getByText("single")).toBeInTheDocument();
    // description + hint
    expect(
      screen.getByText("Digest each uploaded file into the Hub's memory."),
    ).toBeInTheDocument();
    expect(screen.getByText("Drop files into inputs/.")).toBeInTheDocument();
    // footer
    expect(
      screen.getByText(/Headless · API-triggerable · you approve before any commit\./),
    ).toBeInTheDocument();
  });

  it("launches the chosen workflow by id and closes the menu", () => {
    const onLaunch = vi.fn();
    render(<RunWorkflowPicker workflows={WORKFLOWS} onLaunch={onLaunch} />);
    fireEvent.click(screen.getByTestId("run-workflow-button"));
    fireEvent.click(screen.getByTestId("run-workflow-card-consolidate"));
    expect(onLaunch).toHaveBeenCalledWith("consolidate");
    expect(screen.queryByTestId("run-workflow-menu")).not.toBeInTheDocument();
  });

  it("is inert when disabled", () => {
    const onLaunch = vi.fn();
    render(<RunWorkflowPicker workflows={WORKFLOWS} onLaunch={onLaunch} disabled />);
    const btn = screen.getByTestId("run-workflow-button");
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(screen.queryByTestId("run-workflow-menu")).not.toBeInTheDocument();
  });
});
