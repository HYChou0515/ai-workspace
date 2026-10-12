// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NewItemPicker } from "./NewItemPicker";

afterEach(cleanup);

describe("NewItemPicker", () => {
  it("names the one thing it makes: a new chat, with no ellipsis because nothing is left to pick", () => {
    // It used to open a menu of a free chat plus the profile's workflows, so it
    // read "New…" (#466). A workflow is not started from a chat any more
    // (plan-workflow-out-of-chat D3 / D7), which left a menu of one item.
    render(<NewItemPicker onFreeChat={() => {}} />);
    expect(screen.getByTestId("new-item-button")).toHaveTextContent("New chat");
    expect(screen.getByTestId("new-item-button")).not.toHaveTextContent("…");
  });

  it("opens a free chat in one click, with no menu", () => {
    const onFreeChat = vi.fn();
    render(<NewItemPicker onFreeChat={onFreeChat} />);
    fireEvent.click(screen.getByTestId("new-item-button"));
    expect(onFreeChat).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("new-item-menu")).not.toBeInTheDocument();
  });

  it("is inert when disabled", () => {
    const onFreeChat = vi.fn();
    render(<NewItemPicker onFreeChat={onFreeChat} disabled />);
    const btn = screen.getByTestId("new-item-button");
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onFreeChat).not.toHaveBeenCalled();
  });
});
