// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { StepStateDTO } from "../api/workflows";
import { WorkflowTimeline } from "./WorkflowTimeline";

afterEach(cleanup);

const step = (over: Partial<StepStateDTO>): StepStateDTO => ({
  phase: "p",
  name: "n",
  key: "",
  status: "passed",
  attempts: 0,
  reason: "",
  started: null,
  ended: null,
  ...over,
});

describe("WorkflowTimeline", () => {
  it("draws a bar per timed step and a break marker for the idle wait between them", () => {
    render(
      <WorkflowTimeline
        live={false}
        steps={[
          step({ name: "classify", started: 0, ended: 10_000 }),
          // 5 minutes of awaiting-human, then commit
          step({ name: "commit", started: 310_000, ended: 320_000 }),
        ]}
      />,
    );
    const bars = screen.getAllByTestId("wf-timeline-bar");
    expect(bars).toHaveLength(2);
    expect(screen.getByText("classify")).toBeInTheDocument();
    expect(screen.getByText("commit")).toBeInTheDocument();
    // the idle wait is compressed to a marker carrying its real duration (~5 min)
    expect(screen.getByTestId("wf-timeline-gap")).toBeInTheDocument();
    expect(screen.getByText(/5/)).toBeInTheDocument();
  });

  it("shows an empty state when no step has started", () => {
    render(<WorkflowTimeline live={false} steps={[step({ started: null })]} />);
    expect(screen.getByTestId("wf-timeline-empty")).toBeInTheDocument();
  });

  it("follows now on a live run, then surfaces 'jump to now' after a manual zoom", () => {
    render(<WorkflowTimeline live steps={[step({ name: "a", started: 0, ended: 1000 })]} />);
    // following by default → no jump-to-now affordance
    expect(screen.queryByTestId("wf-timeline-now")).toBeNull();
    // zooming is a manual interaction → drops follow, surfaces the affordance
    fireEvent.click(screen.getByTestId("wf-timeline-zoom-in"));
    expect(screen.getByTestId("wf-timeline-now")).toBeInTheDocument();
    // jumping back re-enters follow and hides it again
    fireEvent.click(screen.getByTestId("wf-timeline-now"));
    expect(screen.queryByTestId("wf-timeline-now")).toBeNull();
  });
});
