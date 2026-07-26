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
    { name: "assignee", role: "actor" },
  ],
  form: [],
};
const users = [
  { id: "alice", name: "Alice Chen", section: "", email: "", photo_url: "" },
  { id: "bob", name: "Bob Liu", section: "", email: "", photo_url: "" },
];
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

  it("orders rows by the manual rank, so the Timeline matches the board/table order", () => {
    const spec = { view: "gantt" as const, entity: "issue", span: "span", label: "title" };
    const ent = [
      rec(1, { title: "A", span: "2026-01-01/2026-01-05", rank: 3 }),
      rec(2, { title: "B", span: "2026-01-01/2026-01-05", rank: 1 }),
      rec(3, { title: "C", span: "2026-01-01/2026-01-05", rank: 2 }),
    ];
    render(<GanttView {...props({ spec, entities: ent })} />);
    const labels = Array.from(document.querySelectorAll(".ev-gantt__row-label")).map((n) => n.textContent);
    expect(labels).toEqual(["B", "C", "A"]); // rank 1, 2, 3
  });

  it("makes the gutter row labels draggable to reorder — but inert while a sort is active", () => {
    const base = { view: "gantt" as const, entity: "issue", span: "span", label: "title" };
    const ent = [rec(1, { title: "A", span: "2026-01-01/2026-01-05" }), rec(2, { title: "B", span: "2026-01-06/2026-01-10" })];
    const r1 = render(<GanttView {...props({ spec: base, entities: ent })} />);
    expect(document.querySelectorAll(".ev-gantt__row-label[data-drag]").length).toBe(2); // manual reorder on
    r1.unmount();
    render(<GanttView {...props({ spec: { ...base, sort: [{ field: "title", dir: "asc" as const }] }, entities: ent })} />);
    expect(document.querySelectorAll(".ev-gantt__row-label[data-drag]").length).toBe(0); // sort takes over
  });

  it("shows the assignee's avatar on the bar when spec.assignee is set (① who is responsible)", () => {
    const spec = { view: "gantt" as const, entity: "issue", span: "span", label: "title", assignee: "assignee" };
    render(<GanttView {...props({ spec, users, entities: [rec(1, { title: "A", span: "2026-01-01/2026-01-05", assignee: "alice" })] })} />);
    expect(screen.getByTestId("bar-1-assignee")).toHaveAttribute("title", "Alice Chen");
  });

  it("shows the assignee's NAME on the bar when assignee_display is 'name'", () => {
    const spec = { view: "gantt" as const, entity: "issue", span: "span", label: "title", assignee: "assignee", assignee_display: "name" as const };
    render(<GanttView {...props({ spec, users, entities: [rec(1, { title: "A", span: "2026-01-01/2026-01-05", assignee: "alice" })] })} />);
    expect(screen.getByTestId("bar-1-assignee")).toHaveTextContent("Alice Chen");
  });

  it("hides the assignee when assignee_display is 'none'", () => {
    const spec = { view: "gantt" as const, entity: "issue", span: "span", label: "title", assignee: "assignee", assignee_display: "none" as const };
    render(<GanttView {...props({ spec, users, entities: [rec(1, { title: "A", span: "2026-01-01/2026-01-05", assignee: "alice" })] })} />);
    expect(screen.queryByTestId("bar-1-assignee")).not.toBeInTheDocument();
  });

  it("labels an actor group_by lane with the user's name, not the raw id (② resource view)", () => {
    const spec = { view: "gantt" as const, entity: "issue", span: "span", label: "title", group_by: "assignee" };
    render(<GanttView {...props({ spec, users, entities: [rec(1, { title: "A", span: "2026-01-01/2026-01-05", assignee: "alice" })] })} />);
    expect(screen.getByText("Alice Chen")).toBeInTheDocument();
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

  it("fits a short project to the measured pane so it fills the width by default", () => {
    class FakeRO {
      constructor(private cb: ResizeObserverCallback) {}
      observe() {
        this.cb([{ contentRect: { width: 900 } } as ResizeObserverEntry], this as unknown as ResizeObserver);
      }
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", FakeRO);
    // an 11-day project (daysBetween 10) in a 900px pane (750 after the gutter)
    // auto-fits toward the pane: the ideal ~68px/day is capped at the extended
    // max zoom (56px/day, past the `day` anchor), so the 10-day bar is 560px.
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-01-05/2026-01-15" })] })} />);
    expect(screen.getByTestId("bar-1").style.width).toBe("560px");
    vi.unstubAllGlobals();
  });

  it("labels the axis with custom week codes when the view carries a week rule", () => {
    // A mid-year span, so the week codes don't depend on the (real) clock: the
    // week starting Mon 2026-06-29 is W627 under the user's W{y1}{ww} rule.
    const spec = {
      view: "gantt" as const,
      entity: "issue",
      span: "span",
      label: "title",
      week: { start: "monday" as const, first_week: "jan1" as const, reset: "yearly" as const, boundary: "by_today" as const, label: "W{y1}{ww}" },
    };
    render(<GanttView {...props({ spec, entities: [rec(1, { title: "A", span: "2026-06-29/2026-08-01" })] })} />);
    expect(screen.getByText("W627")).toBeInTheDocument();
  });

  it("with skip_weekends, a bar spanning a weekend collapses to working days only", () => {
    const base = { view: "gantt" as const, entity: "issue", span: "span", label: "title" };
    const ent = [rec(1, { title: "A", span: "2026-07-03/2026-07-13" })]; // Fri → Mon, over a weekend
    const r1 = render(<GanttView {...props({ spec: base, entities: ent })} />);
    const calWidth = screen.getByTestId("bar-1").style.width; // 10 calendar days @ 10px = 100px
    r1.unmount();
    render(<GanttView {...props({ spec: { ...base, skip_weekends: true }, entities: ent })} />);
    const wdWidth = screen.getByTestId("bar-1").style.width; // 6 working days @ 10px = 60px
    expect(Number.parseInt(wdWidth, 10)).toBeLessThan(Number.parseInt(calWidth, 10));
    expect(calWidth).toBe("100px");
    expect(wdWidth).toBe("60px");
  });

  it("marks today when it falls within the chart range", () => {
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2020-01-01/2035-01-01" })] })} />);
    expect(screen.getByTestId("gantt-today")).toBeInTheDocument();
  });
});
