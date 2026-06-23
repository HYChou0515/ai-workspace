// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CollectionsButton } from "./CollectionsButton";

afterEach(cleanup);

describe("CollectionsButton", () => {
  it("nudges with an accent prompt when nothing is selected", () => {
    render(<CollectionsButton count={0} onClick={() => {}} />);
    const btn = screen.getByTestId("collections-button");
    expect(btn).toHaveTextContent("選擇知識庫");
    expect(btn.className).toContain("collections-button--empty");
  });

  it("shows a count badge once collections are selected", () => {
    render(<CollectionsButton count={3} onClick={() => {}} />);
    const btn = screen.getByTestId("collections-button");
    expect(btn).toHaveTextContent("知識庫 (3)");
    expect(btn.className).not.toContain("collections-button--empty");
  });

  it("invokes onClick", () => {
    const onClick = vi.fn();
    render(<CollectionsButton count={1} onClick={onClick} />);
    fireEvent.click(screen.getByTestId("collections-button"));
    expect(onClick).toHaveBeenCalled();
  });
});
