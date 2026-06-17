// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NewChatPicker } from "./NewChatPicker";

afterEach(cleanup);

describe("NewChatPicker", () => {
  it("fires onFreeChat when clicked", () => {
    const onFreeChat = vi.fn();
    render(<NewChatPicker onFreeChat={onFreeChat} />);
    fireEvent.click(screen.getByTestId("new-chat-button"));
    expect(onFreeChat).toHaveBeenCalledTimes(1);
  });

  it("is inert when disabled", () => {
    const onFreeChat = vi.fn();
    render(<NewChatPicker onFreeChat={onFreeChat} disabled />);
    const btn = screen.getByTestId("new-chat-button");
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onFreeChat).not.toHaveBeenCalled();
  });
});
