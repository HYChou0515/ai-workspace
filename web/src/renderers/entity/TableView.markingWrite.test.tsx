// @vitest-environment happy-dom
/**
 * #847/#848 PR 5 P2: selecting rows in an entity `table` writes its marking, so
 * the charts on it light up; a table on no marking keeps its multi-select.
 *
 * What is written: the selected rows projected onto the key columns — the
 * spec's `keys:`, else the marking's own columns — as marking text (the chart
 * reads the same store and lights by `isLit`). With neither, nothing is written
 * and the header's marking control says why.
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityInstance, EntityType } from "../../api/entities";
import { DialogProvider } from "../../components/Dialog";
import { MarkingProvider } from "../../hooks/useMarking";
import { type Marking, MarkingStore } from "../../lib/markings";
import { EntityViewBody, parseViewSpec } from "./EntityViews";

const lotType: EntityType = {
  name: "lot",
  records_path: "lots",
  fields: [
    { name: "lot", role: "text" },
    { name: "status", role: "status", values: ["open", "done"] },
    { name: "fail_rate", role: "text" },
  ],
  form: [],
};

function rec(number: number, fields: Record<string, unknown>): EntityInstance {
  return { number, type_name: "lot", fields, body: "", diagnostics: [] };
}

const LOTS = [
  rec(1, { lot: "L1", status: "open", fail_rate: 0.1 }),
  rec(2, { lot: "L2", status: "open", fail_rate: 0.4 }),
  rec(3, { lot: "L3", status: "done", fail_rate: 3 }),
  rec(4, { lot: "L4", status: "done", fail_rate: 0.2 }),
];
const PATH = "/views/lots.ai.yaml";

function view(text: string, store: MarkingStore, opts: { canWrite?: boolean; onPatch?: () => void } = {}) {
  return render(
    <DialogProvider>
      <MarkingProvider store={store}>
        <EntityViewBody
          spec={parseViewSpec(text)!}
          type={lotType}
          entities={LOTS}
          path={PATH}
          viewKey={`item1:${PATH}`}
          canWrite={opts.canWrite}
          onCreate={vi.fn()}
          onPatch={opts.onPatch ?? vi.fn()}
        />
      </MarkingProvider>
    </DialogProvider>,
  );
}

function held(store: MarkingStore, name = "fail"): Record<string, string[]> | undefined {
  const entry = store.get(name);
  if (!entry) return undefined;
  return Object.fromEntries(Object.entries(entry.marking as Marking).map(([k, v]) => [k, [...v].sort()]));
}

function shownNumbers(): number[] {
  return screen.queryAllByTestId(/^row-open-/).map((td) => Number(td.textContent));
}

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("selecting rows on a marking", () => {
  it("writes the selected rows' `keys:` values, with this view as the source", () => {
    const store = new MarkingStore();
    view("view: table\nentity: lot\nmarking: fail\nkeys: [lot]\n", store);
    fireEvent.click(screen.getByLabelText("select 2"));
    expect(held(store)).toEqual({ lot: ["L2"] });
    expect(store.get("fail")!.source).toBe(PATH);
    fireEvent.click(screen.getByLabelText("select 4"));
    expect(held(store)).toEqual({ lot: ["L2", "L4"] });
  });

  it("writes a number as the chart writes it, so a chart keyed on it lights the row", () => {
    const store = new MarkingStore();
    view("view: table\nentity: lot\nmarking: fail\nkeys: [fail_rate]\n", store);
    fireEvent.click(screen.getByLabelText("select 3"));
    expect(held(store)).toEqual({ fail_rate: ["3"] });
  });

  it("does not filter the table it was made in: every row stays, the selected ones checked and marked", () => {
    const store = new MarkingStore();
    view("view: table\nentity: lot\nmarking: fail\nkeys: [lot]\n", store);
    fireEvent.click(screen.getByLabelText("select 2"));
    expect(shownNumbers()).toEqual([1, 2, 3, 4]);
    expect(screen.getByLabelText("select 2")).toBeChecked();
    expect(screen.getByLabelText("select 1")).not.toBeChecked();
    expect(screen.getByTestId("row-open-2").closest("tr")).toHaveAttribute("data-marked");
    expect(screen.getByRole("status", { name: /marking filter/i })).toHaveTextContent("fail marks 1 of 4 rows");
  });

  it("without `keys:`, writes the columns the marking already holds", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L1"]) }, "/views/chart.ai.yaml");
    view("view: table\nentity: lot\nmarking: fail\n", store);
    fireEvent.click(screen.getByRole("button", { name: "show all" }));
    fireEvent.click(screen.getByLabelText("select 3"));
    expect(held(store)).toEqual({ lot: ["L1", "L3"] });
  });

  it("unchecking a row in a filtered table takes it out of the marking", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L2", "L3"]) }, "/views/chart.ai.yaml");
    view("view: table\nentity: lot\nmarking: fail\n", store);
    expect(screen.getByLabelText("select 2")).toBeChecked();
    fireEvent.click(screen.getByLabelText("select 3"));
    expect(held(store)).toEqual({ lot: ["L2"] });
  });

  it("decides only about the values it holds: a value no row of it carries stays marked", () => {
    // A chart over a wider source marked L9, which this table has no row for;
    // checking and unchecking rows here must not take L9 out of the marking.
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L2", "L9"]) }, "/views/chart.ai.yaml");
    view("view: table\nentity: lot\nmarking: fail\nkeys: [lot]\n", store);
    fireEvent.click(screen.getByRole("button", { name: "show all" }));
    fireEvent.click(screen.getByLabelText("select 3"));
    expect(held(store)).toEqual({ lot: ["L2", "L3", "L9"] });
    fireEvent.click(screen.getByLabelText("select 2"));
    expect(held(store)).toEqual({ lot: ["L3", "L9"] });
  });

  it("a row hidden by the table's own value filter keeps its mark", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L3"]) }, "/views/chart.ai.yaml");
    view("view: table\nentity: lot\nmarking: fail\nkeys: [lot]\n", store);
    fireEvent.click(screen.getByRole("button", { name: "show all" }));
    fireEvent.change(screen.getByLabelText("filter status"), { target: { value: "open" } });
    fireEvent.click(screen.getByLabelText("select 1"));
    expect(held(store)).toEqual({ lot: ["L1", "L3"] });
  });

  it("select all marks every row shown, and again clears the marking", () => {
    const store = new MarkingStore();
    view("view: table\nentity: lot\nmarking: fail\nkeys: [lot]\n", store);
    fireEvent.click(screen.getByLabelText("select all"));
    expect(held(store)).toEqual({ lot: ["L1", "L2", "L3", "L4"] });
    fireEvent.click(screen.getByLabelText("select all"));
    expect(store.get("fail")).toBeUndefined();
  });

  it("is open to a member who cannot edit the records: marking is not a write to them", () => {
    const store = new MarkingStore();
    view("view: table\nentity: lot\nmarking: fail\nkeys: [lot]\n", store, { canWrite: false });
    fireEvent.click(screen.getByLabelText("select 1"));
    expect(held(store)).toEqual({ lot: ["L1"] });
  });

  it("is not batch edit: no batch toolbar opens, and no record is patched", () => {
    const store = new MarkingStore();
    const onPatch = vi.fn();
    view("view: table\nentity: lot\nmarking: fail\nkeys: [lot]\n", store, { onPatch });
    fireEvent.click(screen.getByLabelText("select 1"));
    expect(screen.queryByRole("toolbar", { name: "batch actions" })).not.toBeInTheDocument();
    expect(onPatch).not.toHaveBeenCalled();
  });

  it("a chart's later write moves the checks: the table shows the marking, whoever wrote it", () => {
    const store = new MarkingStore();
    view("view: table\nentity: lot\nmarking: fail\nkeys: [lot]\n", store);
    fireEvent.click(screen.getByLabelText("select 1"));
    act(() => store.set("fail", { lot: new Set(["L4"]) }, "/views/chart.ai.yaml"));
    expect(shownNumbers()).toEqual([4]);
    expect(screen.getByLabelText("select 4")).toBeChecked();
  });
});

describe("a table that cannot write its marking", () => {
  it("writes nothing and says why in the header, when it names no keys and the marking holds nothing", () => {
    const store = new MarkingStore();
    view("view: table\nentity: lot\nmarking: fail\n", store);
    const header = screen.getByRole("combobox", { name: /marking/i }).closest(".ev-marking") as HTMLElement;
    expect(within(header).getByRole("note")).toHaveTextContent(/selecting rows marks nothing/i);
    expect(within(header).getByRole("note")).toHaveTextContent(/keys:/);
    expect(screen.getByLabelText("select 1")).toBeDisabled();
    fireEvent.click(screen.getByLabelText("select 1"));
    expect(store.get("fail")).toBeUndefined();
  });

  it("writes nothing when none of its key columns is a column of this table", () => {
    const store = new MarkingStore();
    view("view: table\nentity: lot\nmarking: fail\nkeys: [wafer]\n", store);
    expect(screen.getByRole("note")).toHaveTextContent(/selecting rows marks nothing/i);
    expect(screen.getByLabelText("select 1")).toBeDisabled();
  });

  it("says nothing once it can write", () => {
    const store = new MarkingStore();
    view("view: table\nentity: lot\nmarking: fail\nkeys: [lot]\n", store);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });
});

describe("a table on no marking keeps its multi-select", () => {
  it("selects for batch edit and drag, and writes no marking", () => {
    const store = new MarkingStore();
    const onPatch = vi.fn();
    view("view: table\nentity: lot\n", store, { onPatch });
    fireEvent.click(screen.getByLabelText("select 1"));
    fireEvent.click(screen.getByLabelText("select 2"));
    const bar = screen.getByRole("toolbar", { name: "batch actions" });
    expect(bar).toHaveTextContent("2 selected");
    fireEvent.change(within(bar).getByLabelText("batch status"), { target: { value: "done" } });
    expect(onPatch).toHaveBeenCalledTimes(2);
    expect(store.names()).toEqual([]);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("putting the view on a marking drops a batch selection made before it", () => {
    const store = new MarkingStore();
    store.set("fail", { lot: new Set(["L4"]) }, "/views/chart.ai.yaml");
    view("view: table\nentity: lot\n", store);
    fireEvent.click(screen.getByLabelText("select 1"));
    expect(screen.getByRole("toolbar", { name: "batch actions" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: /marking/i }), { target: { value: "fail" } });
    expect(screen.queryByRole("toolbar", { name: "batch actions" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: /marking/i }), { target: { value: "" } });
    expect(screen.getByLabelText("select 1")).not.toBeChecked();
    expect(screen.queryByRole("toolbar", { name: "batch actions" })).not.toBeInTheDocument();
  });

  it("a view detached in the header goes back to batch select", () => {
    const store = new MarkingStore();
    view("view: table\nentity: lot\nmarking: fail\nkeys: [lot]\n", store);
    fireEvent.change(screen.getByRole("combobox", { name: /marking/i }), { target: { value: "" } });
    fireEvent.click(screen.getByLabelText("select 1"));
    expect(screen.getByRole("toolbar", { name: "batch actions" })).toHaveTextContent("1 selected");
    expect(store.get("fail")).toBeUndefined();
  });
});
