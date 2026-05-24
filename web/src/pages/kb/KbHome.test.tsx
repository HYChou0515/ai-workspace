// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { mockKbApi, _resetKbMock } from "../../api/kbMock";
import { KbHome } from "./KbHome";

function renderShell() {
  return render(
    <MemoryRouter>
      <KbHome client={mockKbApi} />
    </MemoryRouter>,
  );
}

describe("KbHome shell", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("shows the collections surface by default and switches to chats", async () => {
    renderShell();
    // collections surface (the create input) is visible first
    expect(screen.getByPlaceholderText("New collection name…")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^Chats$/ }));
    // the chats surface is up — its New-chat action is unique to it
    expect(screen.getByRole("button", { name: /new chat/i })).toBeInTheDocument();
  });

  it("opens the Ask-agent drawer from the top bar", async () => {
    renderShell();
    await userEvent.click(screen.getByRole("button", { name: /ask agent/i }));
    await waitFor(() =>
      expect(screen.getByPlaceholderText("Ask the knowledge base…")).toBeInTheDocument(),
    );
  });

  it("opens the doc viewer when a citation is followed end-to-end", async () => {
    const col = await mockKbApi.createCollection("kb");
    renderShell();
    // open drawer, ask, follow the citation → doc viewer renders the document
    await userEvent.click(screen.getByRole("button", { name: /ask agent/i }));
    await userEvent.type(screen.getByPlaceholderText("Ask the knowledge base…"), "why?");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    const cite = await screen.findByRole("button", { name: /reflow\.md/i });
    await userEvent.click(cite);
    await waitFor(() => expect(screen.getByText("Cited passage")).toBeInTheDocument());
    expect(col.resource_id).toBeTruthy();
  });
});
