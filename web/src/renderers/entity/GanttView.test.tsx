// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityInstance, EntityType } from "../../api/entities";
import { GanttView } from "./GanttView";
import { pxPerDay } from "./ganttScale";
import { buildRefIndex } from "./refTraversal";
import type { EntityViewProps } from "./types";

afterEach(cleanup);

const type: EntityType = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text" },
    { name: "span", role: "daterange" },
    { name: "milestone", role: "ref", to: "milestone" },
  ],
  form: [],
};
const rec = (number: number, fields: Record<string, unknown>): EntityInstance => ({
  number,
  type_name: "issue",
  fields,
  body: "",
  diagnostics: [],
});

function props(overrides: Partial<EntityViewProps> = {}): EntityViewProps {
  return {
    spec: { view: "gantt", entity: "issue", span: "span", label: "title" },
    type,
    entities: [],
    onCreate: vi.fn(),
    onPatch: vi.fn(),
    ...overrides,
  };
}

describe("GanttView", () => {
  it("draws a bar only for records with a parseable span", () => {
    render(
      <GanttView
        {...props({ entities: [rec(1, { title: "A", span: "2026-01-01/2026-01-11" }), rec(2, { title: "B" })] })}
      />,
    );
    expect(screen.getByTestId("bar-1")).toBeInTheDocument();
    expect(screen.queryByTestId("bar-2")).not.toBeInTheDocument();
  });

  it("shows a friendly note when nothing has a date range", () => {
    render(<GanttView {...props({ entities: [rec(1, { title: "A" })] })} />);
    expect(screen.getByText(/No records with a date range/)).toBeInTheDocument();
  });

  it("moves a bar by dragging its body and writes the shifted daterange", () => {
    const onPatch = vi.fn();
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-01-10/2026-01-20" })], onPatch })} />);
    const ppd = pxPerDay("week"); // default zoom
    fireEvent.pointerDown(screen.getByTestId("bar-1"), { clientX: 0 });
    fireEvent.pointerMove(window, { clientX: ppd * 3 });
    fireEvent.pointerUp(window, { clientX: ppd * 3 });
    expect(onPatch).toHaveBeenCalledWith(1, { span: "2026-01-13/2026-01-23" });
  });

  it("resizes the end by dragging the right handle", () => {
    const onPatch = vi.fn();
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-01-10/2026-01-20" })], onPatch })} />);
    const ppd = pxPerDay("week");
    fireEvent.pointerDown(screen.getByTestId("bar-1-end"), { clientX: 0 });
    fireEvent.pointerMove(window, { clientX: ppd * 2 });
    fireEvent.pointerUp(window, { clientX: ppd * 2 });
    expect(onPatch).toHaveBeenCalledWith(1, { span: "2026-01-10/2026-01-22" });
  });

  it("does not write when the drag rounds to zero days", () => {
    const onPatch = vi.fn();
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-01-10/2026-01-20" })], onPatch })} />);
    fireEvent.pointerDown(screen.getByTestId("bar-1"), { clientX: 0 });
    fireEvent.pointerUp(window, { clientX: 1 });
    expect(onPatch).not.toHaveBeenCalled();
  });

  it("groups bars into swimlanes by a ref group_by, labeled by the target title", () => {
    const refIndex = buildRefIndex({
      milestone: [{ number: 5, type_name: "milestone", fields: { title: "v1.0" }, body: "", diagnostics: [] }],
    });
    const spec = { view: "gantt" as const, entity: "issue", span: "span", label: "title", group_by: "milestone" };
    render(
      <GanttView {...props({ spec, refIndex, entities: [rec(1, { title: "A", span: "2026-01-01/2026-01-05", milestone: 5 })] })} />,
    );
    expect(screen.getByText("v1.0")).toBeInTheDocument();
  });

  it("snaps to a preset density when its anchor is clicked, changing the bar width", () => {
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-01-01/2026-01-11" })] })} />);
    const weekWidth = screen.getByTestId("bar-1").style.width;
    fireEvent.click(screen.getByRole("button", { name: "zoom month" }));
    expect(screen.getByTestId("bar-1").style.width).not.toBe(weekWidth);
  });

  it("zooms continuously by dragging the density slider", () => {
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-01-01/2026-01-31" })] })} />);
    const slider = screen.getByRole("slider", { name: /zoom/i });
    const before = screen.getByTestId("bar-1").style.width;
    fireEvent.change(slider, { target: { value: "1" } }); // slide fully toward the day anchor
    expect(screen.getByTestId("bar-1").style.width).not.toBe(before);
  });

  it("renders a month context band above the fine ticks (two-tier axis)", () => {
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-01-05/2026-01-20" })] })} />);
    expect(screen.getByText("Jan 2026")).toBeInTheDocument();
  });

  it("fills a wide pane by extending the dated grid past the data (no empty gap)", () => {
    class FakeRO {
      constructor(private cb: ResizeObserverCallback) {}
      observe() {
        this.cb([{ contentRect: { width: 900 } } as ResizeObserverEntry], this as unknown as ResizeObserver);
      }
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", FakeRO);
    // data lives entirely in January; a 900px pane must extend the grid onward
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-01-05/2026-01-15" })] })} />);
    expect(screen.getByText("Feb 2026")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("marks today when it falls within the chart range", () => {
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2020-01-01/2035-01-01" })] })} />);
    expect(screen.getByTestId("gantt-today")).toBeInTheDocument();
  });
});
