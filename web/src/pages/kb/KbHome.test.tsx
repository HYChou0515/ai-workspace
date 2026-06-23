// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen, waitFor } from "@testing-library/react";

import { QueryWrap } from "../../test/queryWrapper";

// KB views read through TanStack Query — wrap every render with a client.
const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(ui, { wrapper: QueryWrap });
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
    // collections surface (the "New collection" action) is visible first
    expect(screen.getByRole("button", { name: /new collection/i })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^對話$/ }));
    // the chats surface is up — its New-chat action is unique to it
    expect(screen.getByRole("button", { name: /new chat/i })).toBeInTheDocument();
  });

  it("#160: the back link is neutral (首頁), not the RCA-flavoured 'Investigations'", () => {
    renderShell();
    expect(screen.getByRole("button", { name: /首頁/ })).toBeInTheDocument();
    expect(screen.queryByText("Investigations")).not.toBeInTheDocument();
  });

  it("lands directly on the chats surface when ?tab=chats", async () => {
    render(
      <MemoryRouter initialEntries={["/kb?tab=chats"]}>
        <KbHome client={mockKbApi} />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("button", { name: /new chat/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /new collection/i })).not.toBeInTheDocument();
  });

  it("opens the Ask-agent drawer from the top bar", async () => {
    renderShell();
    await userEvent.click(screen.getByRole("button", { name: /ask agent/i }));
    await waitFor(() =>
      expect(screen.getByPlaceholderText("Ask the knowledge base…")).toBeInTheDocument(),
    );
  });

  it("opens a NEW chat as a full-page view, not a drawer", async () => {
    await mockKbApi.createCollection("kb");
    renderShell();
    await userEvent.click(screen.getByRole("button", { name: /^對話$/ }));
    expect(screen.getByText(/選擇一個對話/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /new chat/i }));
    // the in-page conversation composer appears…
    expect(await screen.findByPlaceholderText("Ask the knowledge base…")).toBeInTheDocument();
    // …and it is NOT the slide-in drawer dialog
    expect(
      screen.queryByRole("dialog", { name: /Ask the knowledge base/i }),
    ).not.toBeInTheDocument();
  });

  it("shows a newly started chat in the conversations list right away", async () => {
    await mockKbApi.createCollection("kb");
    renderShell();
    await userEvent.click(screen.getByRole("button", { name: /^對話$/ }));
    expect(screen.queryByRole("button", { name: /msgs/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /new chat/i }));
    await userEvent.type(screen.getByPlaceholderText("Ask the knowledge base…"), "hello");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));

    // the thread created on first send appears in the left list (a row w/ msgs)
    await waitFor(() => expect(screen.getByRole("button", { name: /msgs/ })).toBeInTheDocument());
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
