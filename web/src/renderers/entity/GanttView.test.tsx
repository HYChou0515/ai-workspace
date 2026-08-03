// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityInstance, EntityType } from "../../api/entities";
import { GanttView } from "./GanttView";
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
    expect(screen.getByRole("status")).toHaveTextContent("Scheduled 2");
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

    expect(screen.getByTestId("bar-1").style.background).toBe(selectColor("alice").bg);
  });
});
