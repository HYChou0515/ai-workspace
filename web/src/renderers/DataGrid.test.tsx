// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DataGrid } from "./DataGrid";

describe("DataGrid", () => {
  afterEach(cleanup);

  it("renders the first row as headers and the rest as body cells", () => {
    render(<DataGrid rows={[["a", "b"], ["1", "2"]]} />);
    expect(screen.getByRole("columnheader", { name: "a" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "b" })).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("shows the row × column count", () => {
    render(<DataGrid rows={[["a", "b"], ["1", "2"], ["3", "4"]]} />);
    expect(screen.getByText(/2 rows × 2 columns/)).toBeInTheDocument();
  });

  it("caps body rows at maxRows and shows a truncation notice", () => {
    const rows = [["a"], ...Array.from({ length: 10 }, (_, i) => [String(i)])];
    render(<DataGrid rows={rows} maxRows={3} />);
    // Only the first 3 body rows render.
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.queryByText("3")).not.toBeInTheDocument();
    expect(screen.getByText(/showing first 3/)).toBeInTheDocument();
  });

  it("shows an empty-file notice for no rows", () => {
    render(<DataGrid rows={[]} />);
    expect(screen.getByText("Empty file.")).toBeInTheDocument();
  });
});
