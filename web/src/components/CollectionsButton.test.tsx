// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CollectionsButton } from "./CollectionsButton";

afterEach(cleanup);

describe("CollectionsButton", () => {
  it("nudges with a 'set search scope' prompt when nothing is selected (#172)", () => {
    render(<CollectionsButton count={0} onClick={() => {}} />);
    const btn = screen.getByTestId("collections-button");
    expect(btn).toHaveTextContent("設定搜尋範圍");
    expect(btn.className).toContain("collections-button--empty");
  });

  it("frames the selection as the agent's search scope, not a generic count (#172)", () => {
    render(<CollectionsButton count={3} onClick={() => {}} />);
    const btn = screen.getByTestId("collections-button");
    expect(btn).toHaveTextContent("搜尋範圍 · 3");
    expect(btn.className).not.toContain("collections-button--empty");
  });

  it("explains what the button does via a tooltip (#172)", () => {
    render(<CollectionsButton count={3} onClick={() => {}} />);
    expect(screen.getByTestId("collections-button")).toHaveAttribute(
      "title",
      "AI 回答時會在這些知識集裡找資料",
    );
  });

  it("invokes onClick", () => {
    const onClick = vi.fn();
    render(<CollectionsButton count={1} onClick={onClick} />);
    fireEvent.click(screen.getByTestId("collections-button"));
    expect(onClick).toHaveBeenCalled();
  });
});
