// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useState } from "react";

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

  it("flags a read-only sheet on the table, which is what the hover cue keys off", () => {
    // The hover rule used to test the CELL's readOnly. Once a merely SELECTED
    // cell became a read-only input, that test stopped matching anything and the
    // cue silently died. It now keys off this flag instead.
    const { rerender } = render(<SheetGrid rows={GRID} onRowsChange={vi.fn()} />);
    expect(document.querySelector("table.sheet-table")).not.toHaveAttribute("data-readonly");

    rerender(<SheetGrid rows={GRID} readOnly onRowsChange={vi.fn()} />);
    expect(document.querySelector("table.sheet-table")).toHaveAttribute("data-readonly");
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
    await userEvent.dblClick(cell);
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

/** Holds the rows, so an edit re-renders the grid the way the container does. */
function SheetGridHarness({ rows, onUndo }: { rows: string[][]; onUndo?: () => void }) {
  const [current, setCurrent] = useState(rows);
  return <SheetGrid rows={current} onRowsChange={setCurrent} onUndo={onUndo} />;
}

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

  it("clears a selected block with Delete, without removing rows", async () => {
    const onRowsChange = vi.fn();
    render(<SheetGrid rows={SHEET} onRowsChange={onRowsChange} />);

    await userEvent.click(screen.getByLabelText("R1C1"));
    fireEvent.mouseDown(screen.getByLabelText("R2C2"), { shiftKey: true });
    fireEvent.keyDown(screen.getByLabelText("R2C2"), { key: "Delete" });

    expect(onRowsChange).toHaveBeenCalledWith([
      ["wafer", "qty", "note"],
      ["", "", "ok"],
      ["", "", "rework"],
      ["W03", "77", "ok"],
    ]);
  });

  it("cuts: the block reaches the clipboard AND leaves the sheet", async () => {
    const onRowsChange = vi.fn();
    render(<SheetGrid rows={SHEET} onRowsChange={onRowsChange} />);

    await userEvent.click(screen.getByLabelText("R1C1"));
    fireEvent.mouseDown(screen.getByLabelText("R2C2"), { shiftKey: true });

    const written: Record<string, string> = {};
    fireEvent.cut(screen.getByLabelText("R2C2"), {
      clipboardData: { setData: (k: string, v: string) => (written[k] = v), getData: () => "" },
    });

    expect(written["text/plain"]).toBe("W01\t120\nW02\t98\n");
    expect(onRowsChange).toHaveBeenCalledWith([
      ["wafer", "qty", "note"],
      ["", "", "ok"],
      ["", "", "rework"],
      ["W03", "77", "ok"],
    ]);
  });

  function pasteInto(el: Element, text: string) {
    fireEvent.paste(el, { clipboardData: { getData: () => text, setData: () => {} } });
  }

  it("pastes a block into a SINGLE selected cell — the Excel round trip", async () => {
    // The common move: copy a block in Excel, click one cell here, paste. The
    // trigger is what the CLIPBOARD holds, not how much is selected.
    const onRowsChange = vi.fn();
    render(<SheetGrid rows={SHEET} onRowsChange={onRowsChange} />);

    await userEvent.click(screen.getByLabelText("R2C2"));
    pasteInto(screen.getByLabelText("R2C2"), "9\tnine\n8\teight\n");

    expect(onRowsChange).toHaveBeenCalledWith([
      ["wafer", "qty", "note"],
      ["W01", "120", "ok"],
      ["W02", "9", "nine"],
      ["W03", "8", "eight"],
    ]);
  });

  it("grows the sheet when the pasted block runs past the end", async () => {
    const onRowsChange = vi.fn();
    render(<SheetGrid rows={SHEET} onRowsChange={onRowsChange} />);

    await userEvent.click(screen.getByLabelText("R3C3"));
    pasteInto(screen.getByLabelText("R3C3"), "x\ty\n");

    const out = onRowsChange.mock.calls.at(-1)![0] as string[][];
    expect(out[3]).toEqual(["W03", "77", "x", "y"]);
  });

  it("shows the pasted value in the cell that had focus, not the value it used to hold", async () => {
    // The focused cell carries a draft captured when it was focused. A paste
    // changes the value underneath it, so a stale draft would keep painting the
    // OLD value over the new one.
    render(<SheetGridHarness rows={SHEET} />);

    await userEvent.click(screen.getByLabelText("R2C2"));
    pasteInto(screen.getByLabelText("R2C2"), "9\tnine\n");

    expect(screen.getByLabelText("R2C2")).toHaveValue("9");
  });

  it("undoes immediately after a paste, with focus still in the pasted cell", async () => {
    // Same stale draft, second symptom: it made the grid think the user was
    // mid-typing, and Ctrl+Z is deliberately left to the input while typing —
    // so the undo was swallowed exactly when it was most needed.
    const onUndo = vi.fn();
    render(<SheetGridHarness rows={SHEET} onUndo={onUndo} />);

    await userEvent.click(screen.getByLabelText("R2C2"));
    pasteInto(screen.getByLabelText("R2C2"), "9\tnine\n");
    fireEvent.keyDown(screen.getByLabelText("R2C2"), { key: "z", ctrlKey: true });

    expect(onUndo).toHaveBeenCalled();
  });

  it("leaves a single-value paste to the input while EDITING, so you can paste into a word", async () => {
    const onRowsChange = vi.fn();
    render(<SheetGrid rows={SHEET} onRowsChange={onRowsChange} />);

    const cell = screen.getByLabelText("R1C1");
    await userEvent.dblClick(cell);
    pasteInto(cell, "just-text");

    expect(onRowsChange).not.toHaveBeenCalled();
  });

  it("refuses to paste into a read-only sheet", async () => {
    const onRowsChange = vi.fn();
    render(<SheetGrid rows={SHEET} readOnly onRowsChange={onRowsChange} />);

    const cell = screen.getByLabelText("R1C1");
    await userEvent.click(cell);
    pasteInto(cell, "a\tb\n");

    expect(onRowsChange).not.toHaveBeenCalled();
  });

  it("leaves Delete to the input while EDITING, so a value stays editable per character", async () => {
    const onRowsChange = vi.fn();
    render(<SheetGrid rows={SHEET} onRowsChange={onRowsChange} />);

    const cell = screen.getByLabelText("R1C1");
    await userEvent.dblClick(cell);
    fireEvent.keyDown(cell, { key: "Delete" });

    expect(onRowsChange).not.toHaveBeenCalled();
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

describe("SheetGrid — a click selects, a double-click edits", () => {
  afterEach(cleanup);

  const SHEET2 = [
    ["wafer", "qty"],
    ["W01", "120"],
    ["W02", "98"],
  ];

  function clip(el: Element) {
    const written: Record<string, string> = {};
    fireEvent.copy(el, {
      clipboardData: { setData: (k: string, v: string) => (written[k] = v), getData: () => "" },
    });
    return written["text/plain"];
  }

  it("copies a SINGLE clicked cell — the whole reason the mode exists", async () => {
    // Without a mode, clicking put you in a text input and Ctrl+C copied the
    // input's (empty) text selection, so copying one cell was impossible.
    render(<SheetGrid rows={SHEET2} onRowsChange={vi.fn()} />);

    const cell = screen.getByLabelText("R1C2");
    await userEvent.click(cell);

    expect(cell).toHaveAttribute("readonly"); // selected, not editing
    expect(clip(cell)).toBe("120\n");
  });

  it("edits on double-click, and hands the clipboard back to the input", async () => {
    render(<SheetGridHarness rows={SHEET2} />);

    const cell = screen.getByLabelText("R1C2");
    await userEvent.dblClick(cell);
    expect(cell).not.toHaveAttribute("readonly");

    // While editing, copy belongs to the text — the grid must not hijack it.
    expect(clip(cell)).toBeUndefined();
  });

  it("moves the selection with plain arrows", async () => {
    render(<SheetGrid rows={SHEET2} onRowsChange={vi.fn()} />);

    await userEvent.click(screen.getByLabelText("R1C1"));
    fireEvent.keyDown(screen.getByLabelText("R1C1"), { key: "ArrowDown" });

    expect(clip(screen.getByLabelText("R2C1"))).toBe("W02\n");
  });

  it("starts editing when you just type, replacing the value like Excel", async () => {
    render(<SheetGridHarness rows={SHEET2} />);

    const cell = screen.getByLabelText("R1C2");
    await userEvent.click(cell);
    await userEvent.type(cell, "7{Enter}");

    expect(screen.getByLabelText("R1C2")).toHaveValue("7");
  });
});

describe("SheetGrid — row and column selection", () => {
  afterEach(cleanup);

  const SHEET3 = [
    ["wafer", "qty"],
    ["W01", "120"],
    ["W02", "98"],
  ];

  function clipFrom(el: Element) {
    const written: Record<string, string> = {};
    fireEvent.copy(el, {
      clipboardData: { setData: (k: string, v: string) => (written[k] = v), getData: () => "" },
    });
    return written["text/plain"];
  }

  it("selects a whole row from the number gutter", async () => {
    render(<SheetGrid rows={SHEET3} onRowsChange={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Select row 2" }));

    expect(clipFrom(screen.getByLabelText("R2C1"))).toBe("W02\t98\n");
  });

  it("selects a whole column from its header, header cell included", async () => {
    // The header really is a line of the CSV, so a copied column carries its name.
    render(<SheetGrid rows={SHEET3} onRowsChange={vi.fn()} />);

    await userEvent.click(screen.getByLabelText("Column 2 name"));

    expect(clipFrom(screen.getByLabelText("R1C2"))).toBe("qty\n120\n98\n");
    // and the header cell has to LOOK selected, or copying its name is a surprise
    expect(screen.getByLabelText("Column 2 name").closest("th")).toHaveClass("sheet-td--sel");
  });

  it("extends by whole rows when the gutter is shift-clicked", async () => {
    render(<SheetGrid rows={SHEET3} onRowsChange={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Select row 1" }));
    fireEvent.mouseDown(screen.getByRole("button", { name: "Select row 2" }), { shiftKey: true });

    expect(clipFrom(screen.getByLabelText("R1C1"))).toBe("W01\t120\nW02\t98\n");
  });

  it("still renames a column on double-click, so the header obeys the same rule as any cell", async () => {
    render(<SheetGridHarness rows={SHEET3} />);

    const head = screen.getByLabelText("Column 2 name");
    await userEvent.dblClick(head);
    await userEvent.clear(head);
    await userEvent.type(head, "count{Enter}");

    expect(screen.getByLabelText("Column 2 name")).toHaveValue("count");
  });
});
