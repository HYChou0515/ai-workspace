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
