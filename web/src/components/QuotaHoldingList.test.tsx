// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QuotaHoldingList } from "./QuotaHoldingList";

afterEach(cleanup);

const TWO = [
  { itemId: "i-1", title: "晶圓良率分析", cpuCores: 2, memoryBytes: 0 },
  { itemId: "i-2", title: "客訴分類", cpuCores: 2, memoryBytes: 0 },
];

describe("QuotaHoldingList", () => {
  it("names what is holding the quota and what closing it buys back", () => {
    render(<QuotaHoldingList holding={TWO} onClose={() => {}} />);

    expect(screen.getByText(/晶圓良率分析/)).toBeTruthy();
    expect(screen.getByText(/客訴分類/)).toBeTruthy();
    // The figure is the actionable half: "close one" is only a decision once you
    // know which one is worth closing.
    expect(screen.getAllByTestId("holding-cpu")[0].textContent).toContain("2");
  });

  it("closes in place rather than sending the person to another page", () => {
    const onClose = vi.fn();
    render(<QuotaHoldingList holding={TWO} onClose={onClose} />);

    fireEvent.click(screen.getAllByTestId("holding-close")[1]);

    expect(onClose).toHaveBeenCalledWith("i-2");
  });

  it("renders nothing at all when the list is empty", () => {
    // Three causes, none an error: not an environment refusal, a collaborator
    // who may not see the owner's working set, or an older backend. All three
    // must render as absence — not a spinner, not "none found".
    const { container } = render(<QuotaHoldingList holding={[]} onClose={() => {}} />);

    expect(container.textContent).toBe("");
  });

  it("falls back to the id when a title could not be resolved", () => {
    // A deleted item still holds its environment until it is reaped. Addressable
    // beats invisible: a blank row is one the person cannot act on.
    render(
      <QuotaHoldingList
        holding={[{ itemId: "i-9", title: "", cpuCores: 1, memoryBytes: 0 }]}
        onClose={() => {}}
      />,
    );

    expect(screen.getByText(/i-9/)).toBeTruthy();
  });
});
