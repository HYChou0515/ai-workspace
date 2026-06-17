// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { PhaseNode } from "../api/workflows";
import { phaseTone, WorkflowPhaseDiagram } from "./WorkflowPhaseDiagram";

afterEach(cleanup);

const node = (over: Partial<PhaseNode>): PhaseNode => ({
  id: "p",
  title: "P",
  status: "pending",
  done: 0,
  total: 0,
  failed: 0,
  current: false,
  ...over,
});

describe("WorkflowPhaseDiagram", () => {
  it("renders each phase with its status + batch sub-progress", () => {
    render(
      <WorkflowPhaseDiagram
        nodes={[
          node({ id: "classify", title: "Classify", status: "running", done: 3, total: 5, failed: 1, current: true }),
          node({ id: "ingest", title: "Ingest", status: "pending" }),
        ]}
      />,
    );
    const classify = screen.getByText(/Classify/);
    expect(classify.closest("li")).toHaveAttribute("data-status", "running");
    expect(classify).toHaveTextContent("3 / 5 · 1 failed");
    expect(screen.getByText(/Ingest/).closest("li")).toHaveAttribute("data-status", "pending");
  });

  it("renders nothing when there are no phases", () => {
    const { container } = render(<WorkflowPhaseDiagram nodes={[]} />);
    expect(container.querySelector('[data-testid="wf-phase-diagram"]')).toBeNull();
  });

  it("maps statuses to semantic tones", () => {
    expect(phaseTone("passed")).toBe("ok");
    expect(phaseTone("failed")).toBe("err");
    expect(phaseTone("running")).toBe("info");
    expect(phaseTone("awaiting_human")).toBe("warn");
    expect(phaseTone("skipped")).toBe("muted");
  });
});
