// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P1: the entity `table` follows a named marking, as a chart does.
 *
 * Through the dispatcher (`EntityViewBody`, which puts the view on its marking)
 * with the item's real `MarkingStore`: a chart's selection is a write to that
 * store, and the table must answer it. The rows are lit by marking text — the
 * string a chart writes — so a number, a date and a list compare as the chart
 * wrote them (`web/tests/markingRows.corpus.test.ts` holds that reading to
 * the chart's own answer).
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityInstance, EntityType } from "../../api/entities";
import { DialogProvider } from "../../components/Dialog";
import { MarkingProvider } from "../../hooks/useMarking";
import { MarkingStore } from "../../lib/markings";
import { EntityViewBody, parseViewSpec } from "./EntityViews";

const lotType: EntityType = {
  name: "lot",
  records_path: "lots",
  fields: [
    { name: "lot", role: "text" },
    { name: "fail_rate", role: "text" },
    { name: "day", role: "date" },
  ],
  form: [],
};

function rec(number: number, fields: Record<string, unknown>): EntityInstance {
  return { number, type_name: "lot", fields, body: "", diagnostics: [] };
}

const LOTS = [
  rec(1, { lot: "L1", fail_rate: 0.1, day: "2024-01-01" }),
  rec(2, { lot: "L2", fail_rate: 0.4, day: "2024-01-02" }),
  rec(3, { lot: "L3", fail_rate: 3, day: "2024-01-02" }),
  rec(4, { lot: "L4", fail_rate: 0.2, day: "2024-01-03" }),
];

function view(text: string, store: MarkingStore, entities = LOTS, viewKey = "item1:/views/lots.ai.yaml") {
  const spec = parseViewSpec(text)!;
  return render(
    <DialogProvider>
      <MarkingProvider store={store}>
        <EntityViewBody
          spec={spec}
          type={lotType}
          entities={entities}
          path="/views/lots.ai.yaml"
          viewKey={viewKey}
          onCreate={vi.fn()}
          onPatch={vi.fn()}
        />
      </MarkingProvider>
    </DialogProvider>,
  );
}

/** The record numbers of the rows the table shows, in order. */
function shownNumbers(): number[] {
  return screen.queryAllByTestId(/^row-open-/).map((td) => Number(td.textContent));
}

const ON_FAIL = "view: table\nentity: lot\nmarking: fail\n";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("an entity table on a marking that holds a set", () => {
  it("shows only the rows the marking lights, under a bar that says so", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L2", "L3"]) }, "/views/chart.ai.yaml");
    view(ON_FAIL, store);
    expect(shownNumbers()).toEqual([2, 3]);
    const bar = screen.getByRole("status", { name: /marking filter/i });
    expect(bar).toHaveTextContent("filtered by fail · 2 of 4 rows");
    expect(within(bar).getByRole("button", { name: "show all" })).toBeInTheDocument();
  });

  it("answers a chart's later write — the marking is live, not read once", () => {
    const store = new MarkingStore();
    view(ON_FAIL, store);
    expect(shownNumbers()).toEqual([1, 2, 3, 4]);
    act(() => store.set("fail", { lot: new Set(["L4"]) }, "/views/chart.ai.yaml"));
    expect(shownNumbers()).toEqual([4]);
  });

  it("compares a number and a date as the chart writes them", () => {
    // A chart keyed on fail_rate writes String(3) = "3" and String(0.4) = "0.4";
    // one keyed on a date field writes the date's text.
    const store = new MarkingStore();
    store.set("fail", { fail_rate: new Set(["3", "0.4"]) }, "/views/chart.ai.yaml");
    const first = view(ON_FAIL, store);
    expect(shownNumbers()).toEqual([2, 3]);
    first.unmount();
    store.set("fail", { day: new Set(["2024-01-02"]) }, "/views/chart.ai.yaml");
    view(ON_FAIL, store);
    expect(shownNumbers()).toEqual([2, 3]);
  });

  it("lights on the record number, a column every entity table has", () => {
    const store = new MarkingStore();
    store.set("fail", { number: new Set(["4"]) }, "/views/chart.ai.yaml");
    view(ON_FAIL, store);
    expect(shownNumbers()).toEqual([4]);
  });

  it("filters on a column the view does not show", () => {
    const store = new MarkingStore();
    store.set("fail", { day: new Set(["2024-01-03"]) }, "/views/chart.ai.yaml");
    view("view: table\nentity: lot\nmarking: fail\ncolumns: [lot]\n", store);
    expect(shownNumbers()).toEqual([4]);
  });
});

describe("the bar names the columns the marking marks by (P27)", () => {
  // A marking is column -> values, so over two columns it lights every
  // combination; the bar says which columns, so a count larger than the
  // picked rows reads as what it is.
  it("filtering: '· by lot, day' after the count", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L2", "L3"]), day: new Set(["2024-01-02"]) }, "/views/chart.ai.yaml");
    view(ON_FAIL, store);
    const bar = screen.getByRole("status", { name: /marking filter/i });
    expect(bar).toHaveTextContent("filtered by fail · 2 of 4 rows · by lot, day · show all");
  });

  it("showing all: '· by lot' for a one-column marking", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L2"]) }, "/views/chart.ai.yaml");
    view(ON_FAIL, store);
    fireEvent.click(screen.getByRole("button", { name: "show all" }));
    const bar = screen.getByRole("status", { name: /marking filter/i });
    expect(bar).toHaveTextContent("fail marks 1 of 4 rows · by lot · show marked only");
  });
});

describe("show all", () => {
  it("keeps every row and highlights the lit ones, and can go back to filtering", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L2"]) }, "/views/chart.ai.yaml");
    view(ON_FAIL, store);
    fireEvent.click(screen.getByRole("button", { name: "show all" }));
    expect(shownNumbers()).toEqual([1, 2, 3, 4]);
    const marked = screen.getAllByTestId(/^row-open-/).filter((td) => td.closest("tr")!.hasAttribute("data-marked"));
    expect(marked.map((td) => td.textContent)).toEqual(["2"]);
    const bar = screen.getByRole("status", { name: /marking filter/i });
    expect(bar).toHaveTextContent("fail marks 1 of 4 rows");
    fireEvent.click(within(bar).getByRole("button", { name: "show marked only" }));
    expect(shownNumbers()).toEqual([2]);
  });

  it("is this person's choice for this view: kept across a remount, not shared with another view", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L2"]) }, "/views/chart.ai.yaml");
    const first = view(ON_FAIL, store);
    fireEvent.click(screen.getByRole("button", { name: "show all" }));
    first.unmount();
    const again = view(ON_FAIL, store);
    expect(shownNumbers()).toEqual([1, 2, 3, 4]);
    again.unmount();
    view(ON_FAIL, store, LOTS, "item1:/views/other.ai.yaml");
    expect(shownNumbers()).toEqual([2]);
  });

  it("does not highlight rows while filtering — every row shown is lit", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L2"]) }, "/views/chart.ai.yaml");
    view(ON_FAIL, store);
    const row = screen.getByTestId("row-open-2").closest("tr")!;
    expect(row).not.toHaveAttribute("data-marked");
  });
});

describe("a table the marking cannot light", () => {
  it("shows every row and says it has no column in common with the marking", () => {
    const store = new MarkingStore();
    store.set("fail", { wafer: new Set(["W1"]) }, "/views/chart.ai.yaml");
    view(ON_FAIL, store);
    expect(shownNumbers()).toEqual([1, 2, 3, 4]);
    expect(screen.getByRole("status", { name: /marking filter/i })).toHaveTextContent(
      "no column in common with fail",
    );
    expect(screen.queryByRole("button", { name: "show all" })).not.toBeInTheDocument();
  });
});

describe("an empty or cleared marking", () => {
  it("shows every row with no bar", () => {
    const store = new MarkingStore();
    view(ON_FAIL, store);
    expect(shownNumbers()).toEqual([1, 2, 3, 4]);
    expect(screen.queryByRole("status", { name: /marking filter/i })).not.toBeInTheDocument();
  });

  it("brings every row back when the marking is cleared", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L2"]) }, "/views/chart.ai.yaml");
    view(ON_FAIL, store);
    act(() => store.set("fail", {}, "/views/chart.ai.yaml"));
    expect(shownNumbers()).toEqual([1, 2, 3, 4]);
    expect(screen.queryByRole("status", { name: /marking filter/i })).not.toBeInTheDocument();
  });

  it("does not filter a view detached from its marking in the header", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L2"]) }, "/views/chart.ai.yaml");
    view(ON_FAIL, store);
    fireEvent.change(screen.getByRole("combobox", { name: /marking/i }), { target: { value: "" } });
    expect(shownNumbers()).toEqual([1, 2, 3, 4]);
  });
});

describe("the table kind is linkable", () => {
  it("carries the header's marking control on a view whose file names no marking", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L3"]) }, "/views/chart.ai.yaml");
    view("view: table\nentity: lot\n", store);
    expect(shownNumbers()).toEqual([1, 2, 3, 4]);
    fireEvent.change(screen.getByRole("combobox", { name: /marking/i }), { target: { value: "fail" } });
    expect(shownNumbers()).toEqual([3]);
  });
});
