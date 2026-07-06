// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import type { HelpApi } from "../api/help";
import { mockKbApi } from "../api/kbMock";
import { BreadcrumbProvider } from "../hooks/breadcrumbs";
import { QueryWrap } from "../test/queryWrapper";
import { HelpPage } from "./HelpPage";

const helpClient = (over?: Partial<HelpApi>): HelpApi => ({
  getHelpInfo: async () => ({
    collection_id: "help-1",
    documents: [
      { id: "d-guide", path: "getting-started.md", title: "Getting started", kind: "guide" },
      { id: "d-rel", path: "CHANGELOG.md", title: "Changelog", kind: "release_notes" },
    ],
  }),
  getReleases: async () => ({ releases: [] }),
  ...over,
});

const renderPage = (client: HelpApi) =>
  rtlRender(
    <QueryWrap>
      <MemoryRouter>
        <BreadcrumbProvider>
          <HelpPage client={client} chatClient={mockKbApi} />
        </BreadcrumbProvider>
      </MemoryRouter>
    </QueryWrap>,
  );

afterEach(cleanup);

describe("HelpPage (#230)", () => {
  it("links guides to the KB viewer and release notes to the /help/releases view", async () => {
    renderPage(helpClient());
    const guide = await screen.findByRole("link", { name: /Getting started/ });
    expect(guide.getAttribute("href")).toContain("/kb/doc/d-guide");
    // #441: release notes now open the dedicated per-version view, not the raw doc.
    const links = screen.getAllByRole("link");
    expect(links.some((l) => l.getAttribute("href")?.includes("/help/releases"))).toBe(true);
    expect(screen.queryByRole("link", { name: /Changelog/ })).not.toBeInTheDocument();
  });

  it("embeds an AI chat with no collection picker (locked to the Help collection)", async () => {
    renderPage(helpClient());
    // The chat composer renders once the Help collection id resolves…
    await screen.findByPlaceholderText("Ask the knowledge base…");
    // …and it carries no collection picker (it's locked to the Help collection).
    expect(screen.queryByText("Search in")).not.toBeInTheDocument();
  });
});
