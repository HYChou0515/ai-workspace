// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { _resetKbMock, mockKbApi } from "../../api/kbMock";
import { QueryWrap } from "../../test/queryWrapper";
import { kbRoutes } from "./kbRoutes";

// KB views read through TanStack Query — wrap every render with a client.
const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

/** Surfaces the current URL (path + search) so tests can assert navigation. */
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

/** Mount the whole KB route subtree (the App's single source of truth for KB
 * routes) at `path`, wired with the in-memory mock client. */
function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>{kbRoutes(mockKbApi)}</Routes>
      <LocationProbe />
    </MemoryRouter>,
  );
}

describe("KB routes", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("redirects /kb to the collections grid", async () => {
    renderAt("/kb");
    // the grid landing is up (its New-collection action is unique to it)…
    expect(await screen.findByRole("button", { name: /new collection/i })).toBeInTheDocument();
    // …and the URL settled on the canonical collections path
    expect(screen.getByTestId("loc")).toHaveTextContent("/kb/collections");
  });

  it("bounces the legacy /kb?tab=chats deep-link to the chats surface", async () => {
    renderAt("/kb?tab=chats");
    expect(await screen.findByRole("button", { name: /new chat/i })).toBeInTheDocument();
    expect(screen.getByTestId("loc")).toHaveTextContent("/kb/chats");
  });

  it("opening a collection card navigates to its own URL", async () => {
    const col = await mockKbApi.createCollection("Reflow SOPs");
    renderAt("/kb/collections");
    await userEvent.click(await screen.findByRole("button", { name: "Open Reflow SOPs" }));
    expect(screen.getByTestId("loc")).toHaveTextContent(
      `/kb/collections/${encodeURIComponent(col.resource_id)}`,
    );
  });

  it("writes the grid filter to the URL when a tab is picked", async () => {
    await mockKbApi.createCollection("Reflow SOPs");
    renderAt("/kb/collections");
    await userEvent.click(await screen.findByRole("button", { name: /^Mine/ }));
    expect(screen.getByTestId("loc")).toHaveTextContent("view=mine");
    // and back to All clears the param (canonical /kb/collections, no ?view)
    await userEvent.click(screen.getByRole("button", { name: /^All/ }));
    expect(screen.getByTestId("loc")).toHaveTextContent("/kb/collections");
    expect(screen.getByTestId("loc")).not.toHaveTextContent("view=");
  });

  it("opening a collection lands on its Documents tab", async () => {
    const col = await mockKbApi.createCollection("Reflow SOPs");
    renderAt("/kb/collections");
    await userEvent.click(await screen.findByRole("button", { name: "Open Reflow SOPs" }));
    // the bare collection path redirects to the Documents tab
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent(
        `/kb/collections/${encodeURIComponent(col.resource_id)}/documents`,
      ),
    );
  });

  it("routes the collection tab bar (Context Cards / Wiki)", async () => {
    const col = await mockKbApi.createCollection("Reflow SOPs", "", { useRag: true, useWiki: true });
    renderAt(`/kb/collections/${encodeURIComponent(col.resource_id)}`);
    // Glossary tab (#173 rename) → /cards, with its search affordance
    await userEvent.click(await screen.findByRole("tab", { name: "詞彙表" }));
    expect(screen.getByTestId("loc")).toHaveTextContent("/cards");
    expect(await screen.findByLabelText("Search cards")).toBeInTheDocument();
    // Wiki tab → /wiki (the collection has a wiki)
    await userEvent.click(screen.getByRole("tab", { name: "Wiki" }));
    expect(screen.getByTestId("loc")).toHaveTextContent("/wiki");
  });

  it("deep-links straight to the Context Cards tab", async () => {
    const col = await mockKbApi.createCollection("Reflow SOPs");
    renderAt(`/kb/collections/${encodeURIComponent(col.resource_id)}/cards`);
    expect(await screen.findByLabelText("Search cards")).toBeInTheDocument();
    expect(screen.getByTestId("loc")).toHaveTextContent("/cards");
  });

  it("hides the Wiki tab and redirects /wiki when the collection has no wiki", async () => {
    const col = await mockKbApi.createCollection("Reflow SOPs"); // useWiki defaults false
    renderAt(`/kb/collections/${encodeURIComponent(col.resource_id)}/wiki`);
    // no wiki → the /wiki URL falls back to Documents…
    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent("/documents"));
    // …and there's no Wiki tab in the bar
    expect(screen.queryByRole("tab", { name: "Wiki" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "文件" })).toBeInTheDocument();
  });

  it("keeps the Documents tab highlighted with a doc open under it (#93)", async () => {
    const col = await mockKbApi.createCollection("Reflow SOPs");
    const file = new File(["# Guide"], "guide.md", { type: "text/markdown" });
    await mockKbApi.uploadDocument(col.resource_id, file, "guide.md");
    // a leaf URL under the documents tab…
    renderAt(`/kb/collections/${encodeURIComponent(col.resource_id)}/documents/guide.md`);
    // …still marks the Documents tab active (NavLink matches its descendants)
    const tab = await screen.findByRole("tab", { name: "文件" });
    await waitFor(() => expect(tab).toHaveClass("is-active"));
  });

  // ---- P6: chats are URL-addressable ----

  it("routes a new chat and swaps /new for the real id on first message", async () => {
    await mockKbApi.createCollection("kb");
    renderAt("/kb/chats");
    await userEvent.click(await screen.findByRole("button", { name: /new chat/i }));
    expect(screen.getByTestId("loc")).toHaveTextContent("/kb/chats/new");

    await userEvent.type(screen.getByPlaceholderText("Ask the knowledge base…"), "hello");
    await userEvent.click(screen.getByRole("button", { name: /^send$/i }));

    // the thread's real id replaces "new" in the URL (no remount: same view)…
    await waitFor(() =>
      expect(screen.getByTestId("loc").textContent).toMatch(/\/kb\/chats\/(?!new\b)[^/]+$/),
    );
    // …and the started thread shows up in the conversation list
    await waitFor(() => expect(screen.getByRole("button", { name: /msgs/ })).toBeInTheDocument());
  });

  it("deep-links straight to an existing conversation", async () => {
    const chat = await mockKbApi.createChat("Reflow thread", []);
    renderAt(`/kb/chats/${encodeURIComponent(chat.resource_id)}`);
    // the conversation composer is up — not the empty "select" pane
    expect(await screen.findByPlaceholderText("Ask the knowledge base…")).toBeInTheDocument();
    expect(screen.queryByText(/Select a conversation/i)).not.toBeInTheDocument();
  });

  it("clicking a conversation row routes to its own URL", async () => {
    const chat = await mockKbApi.createChat("Reflow thread", []);
    renderAt("/kb/chats");
    expect(await screen.findByText(/選擇一個對話/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /^Reflow thread/ }));
    expect(screen.getByTestId("loc")).toHaveTextContent(
      `/kb/chats/${encodeURIComponent(chat.resource_id)}`,
    );
  });

  // ---- P7: the citation / doc overlay rides a ?doc= search param ----

  it("follows a citation onto a ?doc=&hl= overlay", async () => {
    await mockKbApi.createCollection("kb");
    renderAt("/kb/collections");
    await userEvent.click(await screen.findByRole("button", { name: /ask agent/i }));
    await userEvent.type(screen.getByPlaceholderText("Ask the knowledge base…"), "why?");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    await userEvent.click(await screen.findByRole("button", { name: /reflow\.md/i }));

    // the citation opens the doc overlay AND records it in the URL
    await waitFor(() => expect(screen.getByText("引用段落")).toBeInTheDocument());
    expect(screen.getByTestId("loc")).toHaveTextContent("doc=");
    expect(screen.getByTestId("loc")).toHaveTextContent("hl=");
  });

  it("opens the overlay from ?doc= and closing keeps the other params", async () => {
    renderAt("/kb/collections?view=mine&doc=col-1/me/reflow.md&hl=zone");
    expect(await screen.findByRole("dialog", { name: "Document" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    // doc/hl are gone but the grid filter survives
    expect(screen.getByTestId("loc")).toHaveTextContent("view=mine");
    expect(screen.getByTestId("loc")).not.toHaveTextContent("doc=");
    expect(screen.getByTestId("loc")).not.toHaveTextContent("hl=");
  });
});
