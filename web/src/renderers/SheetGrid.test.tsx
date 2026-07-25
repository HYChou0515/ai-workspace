// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
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

    await userEvent.click(screen.getByLabelText("R2C1")); // focus the W01 row
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

    expect(screen.getByLabelText("R2C1")).toHaveValue("W01"); // view reordered
    expect(onRowsChange).not.toHaveBeenCalled(); // file untouched
  });

  it("edits the FILE row the sorted cell came from, not the row it is displayed at", async () => {
    const onRowsChange = vi.fn();
    render(<SheetGrid rows={DATA} onRowsChange={onRowsChange} />);

    await userEvent.click(screen.getByRole("button", { name: /sort by wafer/i }));
    const cell = screen.getByLabelText("R2C1"); // displays W01, which is file row 2
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

  it("renders only a window of rows for a large file", () => {
    const big = [["h"], ...Array.from({ length: 1000 }, (_, i) => [`r${i}`])];
    render(<SheetGrid rows={big} onRowsChange={vi.fn()} />);

    // Far fewer inputs than rows — the DOM stays light regardless of file size.
    expect(screen.getAllByRole("textbox").length).toBeLessThan(100);
  });
});
