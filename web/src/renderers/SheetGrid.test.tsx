// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SheetGrid } from "./SheetGrid";

const GRID = [
  ["wafer", "qty"],
  ["W01", "120"],
];

describe("SheetGrid — structural edits", () => {
  afterEach(cleanup);

  it("inserts a row below the focused cell", async () => {
    const onRowsChange = vi.fn();
    render(<SheetGrid rows={GRID} onRowsChange={onRowsChange} />);

    await userEvent.click(screen.getByLabelText("R1C1")); // focus the W01 row
    await userEvent.click(screen.getByRole("button", { name: "Insert row below" }));

    expect(onRowsChange).toHaveBeenCalledWith([["wafer", "qty"], ["W01", "120"], ["", ""]]);
  });

  it("hides every structural affordance when read-only", () => {
    render(<SheetGrid rows={GRID} readOnly onRowsChange={vi.fn()} />);

    expect(screen.queryByRole("button", { name: "Insert row below" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete row" })).not.toBeInTheDocument();
  });
});

describe("SheetGrid — sorting is a view, not a rewrite", () => {
  afterEach(cleanup);

  const DATA = [
    ["wafer", "qty"],
    ["W02", "98"],
    ["W01", "120"],
  ];

  it("reorders what is shown without reporting any change to the file", async () => {
    const onRowsChange = vi.fn();
    render(<SheetGrid rows={DATA} onRowsChange={onRowsChange} />);

    await userEvent.click(screen.getByRole("button", { name: /sort by wafer/i }));

    expect(screen.getByLabelText("R1C1")).toHaveValue("W01"); // view reordered
    expect(onRowsChange).not.toHaveBeenCalled(); // file untouched
  });

  it("edits the FILE row the sorted cell came from, not the row it is displayed at", async () => {
    const onRowsChange = vi.fn();
    render(<SheetGrid rows={DATA} onRowsChange={onRowsChange} />);

    await userEvent.click(screen.getByRole("button", { name: /sort by wafer/i }));
    const cell = screen.getByLabelText("R1C1"); // displays W01, which is file row 2
    await userEvent.clear(cell);
    await userEvent.type(cell, "W99{Enter}");

    expect(onRowsChange).toHaveBeenCalledWith([
      ["wafer", "qty"],
      ["W02", "98"],
      ["W99", "120"],
    ]);
  });

  it("writes the sorted order to the file only when explicitly applied", async () => {
    const onRowsChange = vi.fn();
    render(<SheetGrid rows={DATA} onRowsChange={onRowsChange} />);

    await userEvent.click(screen.getByRole("button", { name: /sort by wafer/i }));
    await userEvent.click(screen.getByRole("button", { name: "Apply this order to the file" }));

    expect(onRowsChange).toHaveBeenCalledWith([
      ["wafer", "qty"],
      ["W01", "120"],
      ["W02", "98"],
    ]);
  });

  it("keeps the selection on the same ROW after the order is applied to the file", async () => {
    // Applying the order rewrites the file, so the focused cell's file index
    // moves. If the selection kept the stale index, the very next toolbar action
    // would land on a different row than the one the user is looking at.
    const onRowsChange = vi.fn();
    const { rerender } = render(<SheetGrid rows={DATA} onRowsChange={onRowsChange} />);

    await userEvent.click(screen.getByRole("button", { name: /sort by wafer/i }));
    await userEvent.click(screen.getByLabelText("R1C1")); // W01 — file row 2
    await userEvent.click(screen.getByRole("button", { name: "Apply this order to the file" }));

    const applied = onRowsChange.mock.calls.at(-1)![0] as string[][];
    onRowsChange.mockClear();
    rerender(<SheetGrid rows={applied} onRowsChange={onRowsChange} />);

    // W01 now sits at file row 1; inserting below it must land at index 2.
    await userEvent.click(screen.getByRole("button", { name: "Insert row below" }));
    expect(onRowsChange).toHaveBeenCalledWith([
      ["wafer", "qty"],
      ["W01", "120"],
      ["", ""],
      ["W02", "98"],
    ]);
  });

  it("renders only a window of rows for a large file", () => {
    const big = [["h"], ...Array.from({ length: 1000 }, (_, i) => [`r${i}`])];
    render(<SheetGrid rows={big} onRowsChange={vi.fn()} />);

    // Far fewer inputs than rows — the DOM stays light regardless of file size.
    expect(screen.getAllByRole("textbox").length).toBeLessThan(100);
  });
});

describe("SheetGrid — degradation", () => {
  afterEach(cleanup);

  it("numbers the DATA rows from 1 — the header is not row 1", () => {
    // The header sits in its own band with no number, so starting the gutter at
    // 2 reads as "row 1 is missing".
    render(<SheetGrid rows={GRID} onRowsChange={vi.fn()} />);

    const gutters = [...document.querySelectorAll("tbody .sheet-gutter")].map((td) => td.textContent);
    expect(gutters).toEqual(["1"]);
  });

  it("an empty file gets one editable cell, not a blank pane", () => {
    render(<SheetGrid rows={[]} onRowsChange={vi.fn()} />);

    expect(screen.getByLabelText("Column 1 name")).toBeInTheDocument();
  });

  it("marks a ragged row and names it, instead of hiding or dropping it", () => {
    render(
      <SheetGrid
        rows={[
          ["wafer", "qty", "note"],
          ["W01", "120", "ok"],
          ["W02"],
        ]}
        onRowsChange={vi.fn()}
      />,
    );

    // The row is still there and still editable...
    expect(screen.getByLabelText("R2C1")).toHaveValue("W02");
    // ...and the mismatch is stated with the row number and both counts. It
    // rides the row's accessible name (and the gutter's tooltip) rather than an
    // extra visible cell: the warm gutter edge is the visual cue, and a stray
    // "1 of 3 fields" column would push the data sideways.
    const marked = screen.getByRole("row", { name: /row 2/i });
    expect(marked).toHaveAccessibleName(/1 of 3 fields/i);
  });
});

describe("SheetGrid — range selection and the clipboard", () => {
  afterEach(cleanup);

  const SHEET = [
    ["wafer", "qty", "note"],
    ["W01", "120", "ok"],
    ["W02", "98", "rework"],
    ["W03", "77", "ok"],
  ];

  /** Fire a clipboard event the way the browser does, capturing what the grid writes. */
  function copyFrom(el: Element) {
    const written: Record<string, string> = {};
    fireEvent.copy(el, {
      clipboardData: { setData: (kind: string, value: string) => (written[kind] = value), getData: () => "" },
    });
    return written;
  }

  it("copies a shift-selected block as TSV, which is what Excel reads", async () => {
    render(<SheetGrid rows={SHEET} onRowsChange={vi.fn()} />);

    await userEvent.click(screen.getByLabelText("R1C1"));
    fireEvent.mouseDown(screen.getByLabelText("R2C2"), { shiftKey: true });

    expect(copyFrom(screen.getByLabelText("R2C2"))["text/plain"]).toBe("W01\t120\nW02\t98\n");
  });

  it("leaves a single cell's copy to the input, so half a value can still be copied", async () => {
    render(<SheetGrid rows={SHEET} onRowsChange={vi.fn()} />);

    const cell = screen.getByLabelText("R1C1");
    await userEvent.click(cell);

    // No range — the grid must not hijack the event.
    expect(copyFrom(cell)["text/plain"]).toBeUndefined();
  });

  it("extends the selection with Shift+Arrow", async () => {
    render(<SheetGrid rows={SHEET} onRowsChange={vi.fn()} />);

    const cell = screen.getByLabelText("R1C1");
    await userEvent.click(cell);
    fireEvent.keyDown(cell, { key: "ArrowDown", shiftKey: true });
    fireEvent.keyDown(cell, { key: "ArrowRight", shiftKey: true });

    expect(copyFrom(cell)["text/plain"]).toBe("W01\t120\nW02\t98\n");
  });
});
