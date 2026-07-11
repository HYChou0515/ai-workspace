// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { Pager } from "./Pager";

afterEach(cleanup);

describe("Pager", () => {
  it("shows the total count", () => {
    render(<Pager total={3214} offset={0} pageSize={50} onOffset={vi.fn()} />);
    expect(screen.getByText(/3,?214/)).toBeInTheDocument();
  });

  it("disables prev on the first page and advances on next", () => {
    const onOffset = vi.fn();
    render(<Pager total={120} offset={0} pageSize={50} onOffset={onOffset} />);
    expect(screen.getByRole("button", { name: /prev|上一頁/i })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /next|下一頁/i }));
    expect(onOffset).toHaveBeenCalledWith(50);
  });

  it("disables next on the last page and steps back on prev", () => {
    const onOffset = vi.fn();
    render(<Pager total={120} offset={100} pageSize={50} onOffset={onOffset} />);
    expect(screen.getByRole("button", { name: /next|下一頁/i })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /prev|上一頁/i }));
    expect(onOffset).toHaveBeenCalledWith(50);
  });

  it("hides the prev/next nav when everything fits on one page", () => {
    render(<Pager total={12} offset={0} pageSize={50} onOffset={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /next|下一頁/i })).not.toBeInTheDocument();
  });
});
