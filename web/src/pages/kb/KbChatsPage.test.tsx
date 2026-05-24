// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockKbApi, _resetKbMock } from "../../api/kbMock";
import { KbChatsPage } from "./KbChatsPage";

describe("KbChatsPage", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("lists chats and opens one", async () => {
    const onOpenChat = vi.fn();
    const chat = await mockKbApi.createChat("Void thresholds", ["col-1"]);
    render(<KbChatsPage client={mockKbApi} onOpenChat={onOpenChat} />);

    const row = await screen.findByRole("button", { name: /^Void thresholds/ });
    await userEvent.click(row);
    expect(onOpenChat).toHaveBeenCalledWith(chat.resource_id);
  });

  it("deletes a chat", async () => {
    await mockKbApi.createChat("Doomed", []);
    render(<KbChatsPage client={mockKbApi} />);
    const del = await screen.findByRole("button", { name: /Delete Doomed/ });
    await userEvent.click(del);
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Doomed/ })).not.toBeInTheDocument(),
    );
  });

  it("starts a new chat", async () => {
    const onNewChat = vi.fn();
    render(<KbChatsPage client={mockKbApi} onNewChat={onNewChat} />);
    await userEvent.click(screen.getByRole("button", { name: /new chat/i }));
    expect(onNewChat).toHaveBeenCalled();
  });
});
