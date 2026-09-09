// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EntityInstance, EntityType } from "../../api/entities";
import { GanttView } from "./GanttView";
import { actorPalette } from "./actorColor";
import { selectColor } from "./selectColor";
import { pxPerDay } from "./ganttScale";
import { buildRefIndex } from "./refTraversal";
import type { EntityViewProps } from "./types";

afterEach(cleanup);
// A stubbed ResizeObserver must be torn down even when the test that installed
// it FAILS — an inline unstub is skipped by the throwing assertion, and the fake
// then leaks into every later test (they suddenly see a measured pane), turning
// one real failure into a cascade of false ones.
afterEach(() => vi.unstubAllGlobals());

const type: EntityType = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text" },
    { name: "span", role: "daterange" },
    { name: "milestone", role: "ref", to: "milestone" },
    { name: "assignee", role: "actor" },
    {
      name: "urgency",
      role: "status",
      values: ["critical", "high", "medium", "low"],
      colors: { critical: "red", high: "amber", medium: "blue", low: "slate" },
    },
    { name: "status", role: "status", values: ["open", "done"], colors: { open: "blue", done: "green" } },
  ],
  form: [],
};
const urgencySpec = type.fields.find((f) => f.name === "urgency");
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
  // #680 — double-clicking a bar opens the record. Measured in real chromium
  // first (see docs/plan-issue-680.md): the bar's pointerdown calls
  // preventDefault(), which suppresses the compatibility mouse events but NOT
  // click/dblclick, so the plain handler is enough and no self-counted click
  // fallback is needed.
  it("opens the record on a bar double-click", () => {
    const onOpenRecord = vi.fn();
    render(
      <GanttView
        {...props({ entities: [rec(1, { title: "A", span: "2026-01-01/2026-01-11" })], onOpenRecord })}
      />,
    );

    fireEvent.doubleClick(screen.getByTestId("bar-1"));

    expect(onOpenRecord).toHaveBeenCalledWith(1);
  });

  it("does not confuse a double-click with a drag: no span is written", () => {
    const onPatch = vi.fn();
    const onOpenRecord = vi.fn();
    render(
      <GanttView
        {...props({ entities: [rec(1, { title: "A", span: "2026-01-01/2026-01-11" })], onPatch, onOpenRecord })}
      />,
    );
    const bar = screen.getByTestId("bar-1");

    // Two press/release pairs at the SAME x — what a double-click is at the
    // pointer level. A zero-day drag must not write.
    fireEvent.pointerDown(bar, { clientX: 100 });
    fireEvent.pointerUp(window, { clientX: 100 });
    fireEvent.pointerDown(bar, { clientX: 100 });
    fireEvent.pointerUp(window, { clientX: 100 });
    fireEvent.doubleClick(bar);

    expect(onPatch).not.toHaveBeenCalled();
    expect(onOpenRecord).toHaveBeenCalledTimes(1);
  });

  it("leaves the bar inert when nothing can open a record", () => {
    render(
      <GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-01-01/2026-01-11" })] })} />,
    );
    // No opener wired → the gesture is a no-op rather than a crash.
    expect(() => fireEvent.doubleClick(screen.getByTestId("bar-1"))).not.toThrow();
  });

  it("draws a bar for every record, proposing dates for the ones without (#785)", () => {
    // This reverses the rule it replaces. Leaving a dateless record off the
    // chart does not read as "no dates yet" — it reads as no such work, so the
    // issue nobody has scheduled is exactly the one that disappears. It gets a
    // bar, drawn as a proposal rather than passed off as a plan.
    render(
      <GanttView
        {...props({ entities: [rec(1, { title: "A", span: "2026-01-01/2026-01-11" }), rec(2, { title: "B" })] })}
      />,
    );
    expect(screen.getByTestId("bar-1")).toBeInTheDocument();
    expect(screen.getByTestId("bar-1")).not.toHaveAttribute("data-provisional");
    expect(screen.getByTestId("bar-2")).toHaveAttribute("data-provisional", "true");
  });

  it("proposes a week from today for a record with nothing at all", () => {
    const today = new Date().toISOString().slice(0, 10);
    render(<GanttView {...props({ entities: [rec(1, { title: "A" })] })} />);
    // The title carries the span the bar stands for, so the proposal is legible
    // without opening anything.
    expect(screen.getByTestId("bar-1")).toHaveAttribute("title", expect.stringContaining(today));
  });

  it("marks a half-stated span as a proposal too", () => {
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-01-05/" })] })} />);
    expect(screen.getByTestId("bar-1")).toHaveAttribute("data-provisional", "true");
    expect(screen.getByTestId("bar-1")).toHaveAttribute("title", "2026-01-05/2026-01-11");
  });

  it("keeps 'the DATES are a guess' apart from 'the LENGTH is a guess' (#785)", () => {
    // Seen in a real browser, which is the only place it shows: the shipped
    // Timeline carries a `schedule:` block and `exp_days` is optional, so an
    // ordinary unsized issue is already provisional — #786 established that
    // this is the COMMON case. Every bar on the chart wore the dashed edge, and
    // a record with no dates looked exactly like one that simply has no
    // estimate. That is requirement 7 not met: "visibly not specified yet" has
    // to be visible NEXT TO the ordinary rows, not just in principle.
    const spec = {
      view: "gantt" as const,
      entity: "issue",
      span: "span",
      label: "title",
      schedule: { span: "span", duration: "exp_days", flag: "schedule" },
    };
    render(
      <GanttView
        {...props({
          spec,
          entities: [
            rec(1, { title: "sized and dated", span: "2026-01-05/2026-01-07", exp_days: 3 }),
            rec(2, { title: "dated, no estimate", span: "2026-01-05/2026-01-07" }),
            rec(3, { title: "no dates at all" }),
          ],
        })}
      />,
    );
    // Dashed says "a guess" and is worn by both of the last two.
    expect(screen.getByTestId("bar-1")).not.toHaveAttribute("data-provisional");
    expect(screen.getByTestId("bar-2")).toHaveAttribute("data-provisional", "true");
    expect(screen.getByTestId("bar-3")).toHaveAttribute("data-provisional", "true");
    // The second cue is what separates them: only the dates can be derived.
    expect(screen.getByTestId("bar-1")).not.toHaveAttribute("data-derived");
    expect(screen.getByTestId("bar-2")).not.toHaveAttribute("data-derived");
    expect(screen.getByTestId("bar-3")).toHaveAttribute("data-derived", "true");
  });

  it("shows a friendly note only when there are no records at all", () => {
    render(<GanttView {...props({ entities: [] })} />);
    expect(screen.getByText(/No records to chart yet/)).toBeInTheDocument();
  });

  it("draws a task lying entirely in folded time as a line, not as a working day", () => {
    // §1.3 — the chart used to say a Saturday-to-Sunday issue took as long as a
    // Monday one, because the width had a "never below 1" floor. It measures
    // nothing now, and the view keeps it visible with a hairline instead: the
    // record is still there, and its width no longer claims otherwise.
    render(
      <GanttView
        {...props({
          spec: { view: "gantt" as const, entity: "issue", span: "span", label: "title", skip_weekends: true },
          entities: [
            rec(1, { title: "Weekend", span: "2026-01-10/2026-01-11" }),
            rec(2, { title: "Monday", span: "2026-01-12/2026-01-12" }),
          ],
        })}
      />,
    );
    const weekend = Number.parseFloat(screen.getByTestId("bar-1").style.width);
    const monday = Number.parseFloat(screen.getByTestId("bar-2").style.width);
    expect(weekend).toBeGreaterThan(0); // still on screen
    expect(weekend).toBeLessThan(monday); // but not a day's worth of work
    expect(screen.getByTestId("bar-1")).toHaveAttribute("data-empty", "true");
    expect(screen.getByTestId("bar-2")).not.toHaveAttribute("data-empty");
  });

  it("turns a proposal into the record's own dates the moment it is dragged (#785)", () => {
    // What makes the proposal useful rather than noise: agreeing with it is a
    // drag, and the drag writes it down. Nothing else has to know it was ever
    // a guess — the record now states the span, so the dashes go away on their
    // own the next time it is drawn.
    const onPatch = vi.fn();
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-01-05/" })], onPatch })} />);
    const ppd = pxPerDay("week"); // default zoom
    fireEvent.pointerDown(screen.getByTestId("bar-1"), { clientX: 0 });
    fireEvent.pointerMove(window, { clientX: ppd * 2 });
    fireEvent.pointerUp(window, { clientX: ppd * 2 });
    expect(onPatch).toHaveBeenCalledWith(1, { span: "2026-01-07/2026-01-13" });
  });

  describe("a milestone reaches over its issues (#785)", () => {
    const milestoneType: EntityType = {
      name: "milestone",
      records_path: "milestones",
      fields: [
        { name: "title", role: "text" },
        { name: "span", role: "daterange" },
        { name: "issues", role: "backref", from: "issue.milestone" },
      ],
      form: [],
    };
    const roadmap = { view: "gantt" as const, entity: "milestone", span: "span", label: "title" };
    const issues = (...fields: Record<string, unknown>[]) =>
      buildRefIndex({
        issue: fields.map((f, i) => ({
          number: i + 1,
          type_name: "issue",
          fields: f,
          body: "",
          diagnostics: [],
        })),
      });

    it("draws the bar over its own span AND its issues', not just its own", () => {
      render(
        <GanttView
          {...props({
            spec: roadmap,
            type: milestoneType,
            refIndex: issues(
              { title: "early", span: "2026-06-20/2026-06-25", milestone: 1 },
              { title: "late", span: "2026-07-10/2026-07-20", milestone: 1 },
              { title: "someone else's", span: "2026-01-01/2026-12-31", milestone: 2 },
            ),
            entities: [rec(1, { title: "M1", span: "2026-07-01/2026-07-05" })],
          })}
        />,
      );
      expect(screen.getByTestId("bar-1")).toHaveAttribute("title", "2026-06-20/2026-07-20");
    });

    it("leaves the milestone's own file alone — the reach is drawn, never written", () => {
      // Writing the union back would let the milestone's own lower bound creep
      // earlier every time one of its issues moved earlier, and the next
      // Recalculate would take the crept value as the bound. Every step looks
      // like arithmetic; the schedule drifts anyway.
      const onPatch = vi.fn();
      const onPatchAnchor = vi.fn();
      render(
        <GanttView
          {...props({
            spec: roadmap,
            type: milestoneType,
            onPatch,
            onPatchAnchor,
            refIndex: issues({ title: "early", span: "2026-06-20/2026-06-25", milestone: 1 }),
            entities: [rec(1, { title: "M1", span: "2026-07-01/2026-07-05" })],
          })}
        />,
      );
      expect(onPatch).not.toHaveBeenCalled();
      expect(onPatchAnchor).not.toHaveBeenCalled();
    });

    it("moves the milestone's OWN dates when the bar is dragged, not the reach", () => {
      // The bar you grab is wider than the record — dragging has to change what
      // the record SAYS, or the milestone would swallow its own issues' extent
      // on the first drag and never give it back.
      const onPatch = vi.fn();
      render(
        <GanttView
          {...props({
            spec: roadmap,
            type: milestoneType,
            onPatch,
            refIndex: issues({ title: "early", span: "2026-06-20/2026-06-25", milestone: 1 }),
            entities: [rec(1, { title: "M1", span: "2026-07-01/2026-07-05" })],
          })}
        />,
      );
      const ppd = pxPerDay("week");
      fireEvent.pointerDown(screen.getByTestId("bar-1"), { clientX: 0 });
      fireEvent.pointerMove(window, { clientX: ppd * 2 });
      fireEvent.pointerUp(window, { clientX: ppd * 2 });
      expect(onPatch).toHaveBeenCalledWith(1, { span: "2026-07-03/2026-07-07" });
    });

    it("follows the cursor while dragging, showing the dates being written", () => {
      // The bar at rest is wider than the record. If the drag keeps drawing the
      // REACH, the left edge stays pinned to the earliest issue and a "move"
      // reads as a stretch — the bar does not follow the pointer, and the title
      // shows a range that is not what gets saved. Someone who drags again
      // because "it didn't take" walks the stored dates further every time.
      //
      // So the drag draws the milestone's OWN span: the thing being moved, at
      // the place the cursor put it. The reach comes back on release.
      const onPatch = vi.fn();
      render(
        <GanttView
          {...props({
            spec: roadmap,
            type: milestoneType,
            onPatch,
            refIndex: issues({ title: "early", span: "2026-06-20/2026-06-25", milestone: 1 }),
            entities: [rec(1, { title: "M1", span: "2026-07-01/2026-07-05" })],
          })}
        />,
      );
      const bar = screen.getByTestId("bar-1");
      const restWidth = bar.style.width;
      const ppd = pxPerDay("week");

      fireEvent.pointerDown(bar, { clientX: 0 });
      fireEvent.pointerMove(window, { clientX: ppd * 3 });

      const dragging = screen.getByTestId("bar-1");
      // Five days wide — the record's own span — wherever the reach starts.
      expect(Number.parseFloat(dragging.style.width)).toBeCloseTo(5 * ppd);
      expect(Number.parseFloat(dragging.style.width)).toBeLessThan(Number.parseFloat(restWidth));
      // And it says what it is about to write, not what it was drawing before.
      expect(dragging).toHaveAttribute("title", "2026-07-04/2026-07-08");

      fireEvent.pointerUp(window, { clientX: ppd * 3 });
      expect(onPatch).toHaveBeenCalledWith(1, { span: "2026-07-04/2026-07-08" });
    });

    it("does not let an issue's PROPOSED dates stretch the milestone", () => {
      // An unscheduled issue is drawn as a week from today. Letting that reach
      // into the roadmap would move a milestone because of a record nobody has
      // scheduled — the chart's own guess, fed back as if it were a plan.
      render(
        <GanttView
          {...props({
            spec: roadmap,
            type: milestoneType,
            refIndex: issues({ title: "unscheduled", milestone: 1 }),
            entities: [rec(1, { title: "M1", span: "2026-07-01/2026-07-05" })],
          })}
        />,
      );
      expect(screen.getByTestId("bar-1")).toHaveAttribute("title", "2026-07-01/2026-07-05");
    });
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

  it("draws a part-day bar as part of a day once the columns are hours (#785)", () => {
    // The WIRING check. An axis assertion is not one: `axisFor` works the grain
    // out from the density itself, so the hour axis appears whether or not the
    // view ever asked for hour columns — a probe that survives the view being
    // hard-wired to days is measuring nothing.
    //
    // Geometry is the tell, and only for a bar that is part of a day: whole
    // days occupy identical space at both grains (that continuity is the point
    // — nothing jumps when the switch happens). So put an eight-hour task next
    // to an all-day one.
    render(
      <GanttView
        {...props({
          entities: [
            rec(1, { title: "All day", span: "2026-01-05/2026-01-05" }),
            rec(2, { title: "Morning", span: "2026-01-05T09:00/2026-01-05T17:00" }),
          ],
        })}
      />,
    );
    // In day columns the chart has no way to say one of them is eight hours.
    expect(screen.getByTestId("bar-2").style.width).toBe(screen.getByTestId("bar-1").style.width);
    expect(screen.queryByText("Mon 5 Jan")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("slider", { name: /zoom/i }), { target: { value: "1" } });

    const allDay = Number.parseFloat(screen.getByTestId("bar-1").style.width);
    const morning = Number.parseFloat(screen.getByTestId("bar-2").style.width);
    expect(morning).toBeCloseTo(allDay / 3); // eight of the day's twenty-four hours
    expect(screen.getByText("Mon 5 Jan")).toBeInTheDocument();
  });

  it("gives the night no width once the view names a working day (#785)", () => {
    render(
      <GanttView
        {...props({
          spec: {
            view: "gantt" as const,
            entity: "issue",
            span: "span",
            label: "title",
            work_hours: { from: 7, to: 21 },
          },
          entities: [
            rec(1, { title: "All day", span: "2026-01-05/2026-01-05" }),
            rec(2, { title: "Overnight", span: "2026-01-05T20:00/2026-01-06T08:00" }),
          ],
        })}
      />,
    );
    fireEvent.change(screen.getByRole("slider", { name: /zoom/i }), { target: { value: "1" } });

    const allDay = Number.parseFloat(screen.getByTestId("bar-1").style.width);
    const overnight = Number.parseFloat(screen.getByTestId("bar-2").style.width);
    // A 07:00–21:00 day is fourteen columns. 20:00 → 08:00 spans twelve hours
    // of clock and two of work — the night between them is not drawn, exactly
    // as a weekend between two working days is not drawn.
    expect(overnight).toBeCloseTo(allDay / 7);
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
    // an 11-day project (both ends counted) in a 900px pane (750 after the
    // gutter) auto-fits toward the pane: the ideal ~68px/day is capped at the
    // extended max zoom (56px/day, past the `day` anchor), so the bar covering
    // all 11 days is 616px.
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-01-05/2026-01-15" })] })} />);
    expect(screen.getByTestId("bar-1").style.width).toBe("616px");
  });

  it("colours the end date: an inclusive Mon→Wed span is three days wide", () => {
    // A `daterange` includes both ends — 7/13–7/15 is a three-day task — and the
    // chart width already counts it that way. A bar that stopped at the START of
    // its end date left that day blank, so the range looked a day short.
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-07-13/2026-07-15" })] })} />);
    expect(screen.getByTestId("bar-1").style.width).toBe(`${3 * pxPerDay("week")}px`);
  });

  it("draws a single-day span one day wide, not a clamped minimum", () => {
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-07-16/2026-07-16" })] })} />);
    expect(screen.getByTestId("bar-1").style.width).toBe(`${pxPerDay("week")}px`);
  });

  it("with skip_weekends, the last WORKING day is coloured too", () => {
    const spec = { view: "gantt" as const, entity: "issue", span: "span", label: "title", skip_weekends: true };
    // Mon 07-20 → Fri 07-24 = five working days, both ends included.
    render(<GanttView {...props({ spec, entities: [rec(1, { title: "A", span: "2026-07-20/2026-07-24" })] })} />);
    expect(screen.getByTestId("bar-1").style.width).toBe(`${5 * pxPerDay("week")}px`);
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
    const calWidth = screen.getByTestId("bar-1").style.width; // 11 calendar days @ 10px = 110px
    r1.unmount();
    render(<GanttView {...props({ spec: { ...base, skip_weekends: true }, entities: ent })} />);
    const wdWidth = screen.getByTestId("bar-1").style.width; // 7 working days @ 10px = 70px
    expect(Number.parseInt(wdWidth, 10)).toBeLessThan(Number.parseInt(calWidth, 10));
    expect(calWidth).toBe("110px");
    expect(wdWidth).toBe("70px");
  });

  it("marks today when it falls within the chart range", () => {
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2020-01-01/2035-01-01" })] })} />);
    expect(screen.getByTestId("gantt-today")).toBeInTheDocument();
  });

  it("offers Recalculate only when the view says which fields carry the schedule", () => {
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-07-01/2026-07-02" })] })} />);
    expect(screen.queryByRole("button", { name: "Recalculate" })).not.toBeInTheDocument();
  });

  it("lays the work out and writes the dates it worked out", () => {
    const onPatch = vi.fn();
    const onPatchAnchor = vi.fn();
    const spec = {
      view: "gantt" as const,
      entity: "issue",
      span: "span",
      label: "title",
      schedule: {
        span: "span",
        duration: "exp_days",
        unit: "exp_days_unit",
        flag: "schedule",
        anchor: "milestone",
        assignee: "assignee",
      },
    };
    const refIndex = buildRefIndex({
      milestone: [
        { number: 1, type_name: "milestone", fields: { title: "M1", span: "2026-07-01/" }, body: "", diagnostics: [] },
      ],
    });
    render(
      <GanttView
        {...props({
          spec,
          refIndex,
          onPatch,
          onPatchAnchor,
          entities: [
            // No dates yet — the record most in need of being scheduled, and the
            // one a chart that only reads dated rows would never see.
            rec(1, { title: "A", assignee: "alice", exp_days: 3, milestone: 1, schedule: "auto" }),
            rec(2, { title: "B", assignee: "alice", exp_days: 2, milestone: 1, schedule: "auto" }),
          ],
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Recalculate" }));
    expect(onPatch).toHaveBeenCalledWith(1, { span: "2026-07-01/2026-07-03" });
    expect(onPatch).toHaveBeenCalledWith(2, { span: "2026-07-06/2026-07-07" });
    // The milestone reaches across its issues.
    expect(onPatchAnchor).toHaveBeenCalledWith(1, { span: "2026-07-01/2026-07-07" });
    // Queried by its text, not by role: now that a dateless record still gets a
    // bar this renders the whole chart, and dnd-kit's own live region is a
    // second role="status" on the page.
    expect(screen.getByText(/Scheduled 2/)).toBeInTheDocument();
  });

  it("marks a bar whose length nobody chose, so a placeholder never reads as a plan", () => {
    const spec = {
      view: "gantt" as const,
      entity: "issue",
      span: "span",
      label: "title",
      schedule: { span: "span", duration: "exp_days", flag: "schedule" },
    };
    render(
      <GanttView
        {...props({
          spec,
          entities: [
            rec(1, { title: "estimated", span: "2026-07-01/2026-07-03", exp_days: 3 }),
            rec(2, { title: "guessed", span: "2026-07-06/2026-07-06" }),
          ],
        })}
      />,
    );
    expect(screen.getByTestId("bar-1")).not.toHaveAttribute("data-provisional");
    expect(screen.getByTestId("bar-2")).toHaveAttribute("data-provisional", "true");
  });

  it("never writes an estimate of zero days, however far the end is dragged (#785)", () => {
    // Losing the width floor means a span lying entirely in folded time
    // measures zero columns — right for a BAR, meaningless for an ESTIMATE.
    // "This takes no days" is not a thing anyone can mean, and the record would
    // read "0 days" ever after. A day is the smallest estimate expressible.
    const onPatch = vi.fn();
    render(
      <GanttView
        {...props({
          spec: {
            view: "gantt" as const,
            entity: "issue",
            span: "span",
            label: "title",
            skip_weekends: true,
            schedule: { span: "span", duration: "exp_days", flag: "schedule" },
          },
          onPatch,
          // Starts on a SATURDAY. Dragging the end handle back clamps it to the
          // start, and Sat→Sat is zero working columns — the case a span that
          // starts on a working day can never reach, because the clamp lands it
          // on that working day.
          entities: [rec(1, { title: "A", span: "2026-01-10/2026-01-16", schedule: "auto" })],
        })}
      />,
    );
    const ppd = pxPerDay("week");
    fireEvent.pointerDown(screen.getByTestId("bar-1-end"), { clientX: 0 });
    fireEvent.pointerUp(window, { clientX: -ppd * 20 });

    expect(onPatch).toHaveBeenCalledTimes(1);
    const [, patch] = onPatch.mock.calls[0];
    expect((patch as Record<string, number>).exp_days).toBeGreaterThanOrEqual(1);
  });

  it("dragging the right edge of an automatic bar changes its DURATION, not its dates", () => {
    // Length is the scheduler's input, position its output. Writing a span here
    // would be overwritten by the next run — and the point of dragging is that
    // what you dragged survives it.
    const onPatch = vi.fn();
    const spec = {
      view: "gantt" as const,
      entity: "issue",
      span: "span",
      label: "title",
      schedule: { span: "span", duration: "exp_days", flag: "schedule" },
    };
    render(
      <GanttView
        {...props({ spec, onPatch, entities: [rec(1, { title: "A", span: "2026-01-05/2026-01-07", exp_days: 3, schedule: "auto" })] })}
      />,
    );
    const ppd = pxPerDay("week");
    fireEvent.pointerDown(screen.getByTestId("bar-1-end"), { clientX: 0 });
    fireEvent.pointerMove(window, { clientX: ppd * 2 });
    fireEvent.pointerUp(window, { clientX: ppd * 2 });
    expect(onPatch).toHaveBeenCalledWith(1, { exp_days: 5 });
  });

  it("dragging a MANUAL bar's right edge still writes dates — its length is its dates", () => {
    const onPatch = vi.fn();
    const spec = {
      view: "gantt" as const,
      entity: "issue",
      span: "span",
      label: "title",
      schedule: { span: "span", duration: "exp_days", flag: "schedule" },
    };
    render(
      <GanttView
        {...props({ spec, onPatch, entities: [rec(1, { title: "A", span: "2026-01-05/2026-01-07", schedule: "manual" })] })}
      />,
    );
    const ppd = pxPerDay("week");
    fireEvent.pointerDown(screen.getByTestId("bar-1-end"), { clientX: 0 });
    fireEvent.pointerMove(window, { clientX: ppd * 2 });
    fireEvent.pointerUp(window, { clientX: ppd * 2 });
    expect(onPatch).toHaveBeenCalledWith(1, { span: "2026-01-05/2026-01-09" });
  });

  it("moving an automatic bar writes the dates and leaves the flag alone", () => {
    // A gesture never flips a flag: your placement holds until the next run,
    // and turning the schedule off stays something you say on purpose.
    const onPatch = vi.fn();
    const spec = {
      view: "gantt" as const,
      entity: "issue",
      span: "span",
      label: "title",
      schedule: { span: "span", duration: "exp_days", flag: "schedule" },
    };
    render(
      <GanttView
        {...props({ spec, onPatch, entities: [rec(1, { title: "A", span: "2026-01-05/2026-01-07", exp_days: 3, schedule: "auto" })] })}
      />,
    );
    const ppd = pxPerDay("week");
    fireEvent.pointerDown(screen.getByTestId("bar-1"), { clientX: 0 });
    fireEvent.pointerMove(window, { clientX: ppd * 3 });
    fireEvent.pointerUp(window, { clientX: ppd * 3 });
    expect(onPatch).toHaveBeenCalledWith(1, { span: "2026-01-08/2026-01-10" });
  });
});

describe("GanttView colour source", () => {
  const span = "2026-01-10/2026-01-20";

  it("leaves bars uncoloured until a source is chosen", () => {
    // The default has to stay what it was, or every existing view changes
    // appearance the day this ships.
    render(<GanttView {...props({ entities: [rec(1, { title: "A", span, urgency: "critical" })] })} />);

    expect(screen.getByTestId("bar-1").style.background).toBe("");
  });

  it("colours a bar from the field the view was told to use", () => {
    render(
      <GanttView
        {...props({
          spec: { view: "gantt", entity: "issue", span: "span", label: "title", color_by: "urgency" },
          entities: [rec(1, { title: "A", span, urgency: "critical" })],
        })}
      />,
    );

    // The same palette the chips use — a second one would put `critical` on
    // two different colours in two places on the same screen.
    expect(screen.getByTestId("bar-1").style.background).toBe(selectColor("critical", urgencySpec).bg);
  });

  it("gives a record with nothing set the neutral slot rather than a hashed colour", () => {
    render(
      <GanttView
        {...props({
          spec: { view: "gantt", entity: "issue", span: "span", label: "title", color_by: "urgency" },
          entities: [rec(1, { title: "A", span })],
        })}
      />,
    );

    expect(screen.getByTestId("bar-1").style.background).toBe(selectColor("", urgencySpec).bg);
  });

  it("can colour by who is doing the work, not only by a select", () => {
    // "by 不同東西做顏色 (status or 緊急度, assignee, etc)" — an actor field is
    // one of the things people want to see at a glance.
    render(
      <GanttView
        {...props({
          spec: { view: "gantt", entity: "issue", span: "span", label: "title", color_by: "assignee" },
          entities: [rec(1, { title: "A", span, assignee: "alice" })],
          users,
        })}
      />,
    );

    expect(screen.getByTestId("bar-1").style.background).toBe(actorPalette(["alice"])("alice").bg);
  });

  it("does NOT colour people out of the select palette", () => {
    // A directory is open-ended, so six slots cannot hold it: `selectColor`
    // gives four people a 44% chance of a collision and seven a certainty, and
    // a repeated colour is a bar claiming the wrong owner. The actor palette
    // generates a hue per person instead — see actorColor.ts.
    render(
      <GanttView
        {...props({
          spec: { view: "gantt", entity: "issue", span: "span", label: "title", color_by: "assignee" },
          entities: [rec(1, { title: "A", span, assignee: "alice" })],
          users,
        })}
      />,
    );

    expect(screen.getByTestId("bar-1").style.background).not.toBe(selectColor("alice").bg);
  });
});

describe("collapsible groups (#690 P4)", () => {
  const span = "2026-01-10/2026-01-20";
  const grouped = {
    view: "gantt" as const,
    entity: "issue",
    span: "span",
    label: "title",
    group_by: "assignee",
  };

  beforeEach(() => window.localStorage.clear());

  it("hides a group's rows when its header is clicked", () => {
    render(
      <GanttView
        {...props({
          spec: grouped,
          entities: [rec(1, { title: "A", span, assignee: "alice" }), rec(2, { title: "B", span, assignee: "bob" })],
          users,
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Alice Chen/ }));

    expect(screen.queryByTestId("bar-1")).not.toBeInTheDocument();
    expect(screen.getByTestId("bar-2")).toBeInTheDocument(); // the other lane is untouched
  });

  it("remembers what this person collapsed, per view", () => {
    // Kept out of the view file on purpose: where somebody is LOOKING is not a
    // decision to make for the rest of the project. The key carries the view
    // so two boards do not collapse each other's lanes.
    const { unmount } = render(
      <GanttView
        {...props({ spec: grouped, entities: [rec(1, { title: "A", span, assignee: "alice" })], users, viewKey: "v1" })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Alice Chen/ }));
    unmount();

    render(
      <GanttView
        {...props({ spec: grouped, entities: [rec(1, { title: "A", span, assignee: "alice" })], users, viewKey: "v1" })}
      />,
    );

    expect(screen.queryByTestId("bar-1")).not.toBeInTheDocument();
  });

  it("does not carry one view's collapse into another", () => {
    const { unmount } = render(
      <GanttView
        {...props({ spec: grouped, entities: [rec(1, { title: "A", span, assignee: "alice" })], users, viewKey: "v1" })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Alice Chen/ }));
    unmount();

    render(
      <GanttView
        {...props({ spec: grouped, entities: [rec(1, { title: "A", span, assignee: "alice" })], users, viewKey: "v2" })}
      />,
    );

    expect(screen.getByTestId("bar-1")).toBeInTheDocument();
  });
});

describe("long labels (#690 P6)", () => {
  const span = "2026-01-10/2026-01-20";
  const long = "Qualify the new photoresist on line 3 before the August shutdown";

  it("carries the whole title on the row even though the column truncates it", () => {
    // The column is ellipsised, and until now the only way to read a cut-off
    // title was to open the record — a double-click to answer "which one is
    // this".
    // The FIRST COLUMN specifically. The bar carries the same text and already
    // has a title of its own — the date range — which this must not take.
    const { container } = render(<GanttView {...props({ entities: [rec(1, { title: long, span })] })} />);

    expect(container.querySelector(".ev-gantt__row-label")).toHaveAttribute("title", long);
    expect(screen.getByTestId("bar-1")).toHaveAttribute("title", "2026-01-10/2026-01-20");
  });

  it("carries the whole group name too", () => {
    // Lane labels truncate in the same column, for the same reason.
    render(
      <GanttView
        {...props({
          spec: { view: "gantt", entity: "issue", span: "span", label: "title", group_by: "assignee" },
          entities: [rec(1, { title: "A", span, assignee: "alice" })],
          users,
        })}
      />,
    );

    expect(screen.getByRole("button", { name: /Alice Chen/ })).toHaveAttribute("title", "Alice Chen");
  });
});

describe("sticky axis (#690 P7)", () => {
  it("keeps the axis pinned inside the chart's own scroller", () => {
    // happy-dom does not lay out, so this pins the CONTRACT the CSS relies on:
    // the axis must be a sticky-positioned child of the element that scrolls,
    // and the scroller must be the gantt's own — not the page — or `top: 0`
    // has nothing to stick to.
    //
    // It does NOT show that the axis stays put — nothing without layout can,
    // and this exact assertion passed while the header scrolled away, because
    // a later `position: relative` in the same CSS rule was winning. That was
    // caught in a real browser and is now guarded by ganttAxisSticky.test.ts;
    // the measurement is in docs/plan-pm-gantt-urgency-and-axis.md §7.
    const { container } = render(
      <GanttView {...props({ entities: [rec(1, { title: "A", span: "2026-01-10/2026-01-20" })] })} />,
    );

    const scroller = container.querySelector(".ev-gantt__scroll");
    const axis = container.querySelector(".ev-gantt__axis");

    expect(scroller).toBeTruthy();
    expect(axis).toBeTruthy();
    expect(scroller!.contains(axis!)).toBe(true);
    expect(axis!.className).toContain("ev-gantt__axis");
  });

  describe("the axis says which day of the week it is (#690 P8)", () => {
    // A week rule is what turns the axis week-first; the seeded views all carry
    // one. `skip_weekends` is what makes the digits stop at 5.
    const weekly = (extra: Record<string, unknown> = {}) => ({
      view: "gantt" as const,
      entity: "issue",
      span: "span",
      label: "title",
      week: { label: "W{y1}{ww}" },
      skip_weekends: true,
      ...extra,
    });
    const week = [rec(1, { title: "A", span: "2026-06-29/2026-07-10" })];
    /** Render, then zoom all the way in — the weekday row is the densest tier,
     * and an unmeasured pane (happy-dom lays nothing out) auto-fits to
     * something far coarser. Clicking the anchor is what a user does. */
    const atDayZoom = (spec: Record<string, unknown>) => {
      const r = render(<GanttView {...props({ spec: spec as EntityViewProps["spec"], entities: week })} />);
      fireEvent.click(screen.getByLabelText("zoom day"));
      return r;
    };

    it("labels every column with its weekday, under the week it belongs to", () => {
      atDayZoom(weekly());
      for (const digit of ["1", "2", "3", "4", "5"]) {
        expect(screen.getAllByText(digit).length).toBeGreaterThan(0);
      }
      expect(screen.getAllByText("W627").length).toBeGreaterThan(0); // the week band
    });

    it("carries the day of the month only when the view asks for it", () => {
      const plain = atDayZoom(weekly());
      expect(document.querySelector(".ev-gantt__tick-sub")).toBeNull();
      plain.unmount();

      atDayZoom(weekly({ day_of_month: "always" }));
      expect(document.querySelector(".ev-gantt__tick-sub")).not.toBeNull();
    });

    it("puts the whole date on hover once the day of the month is in play", () => {
      atDayZoom(weekly({ day_of_month: "hover" }));
      const ticks = [...document.querySelectorAll(".ev-gantt__tick")];
      expect(ticks.length).toBeGreaterThan(0);
      expect(ticks.every((el) => /^\d{4}-\d{2}-\d{2}$/.test(el.getAttribute("title") ?? ""))).toBe(true);
    });
  });
});