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
  it("links each help document to the KB document viewer", async () => {
    renderPage(helpClient());
    const guide = await screen.findByRole("link", { name: /Getting started/ });
    expect(guide.getAttribute("href")).toContain("/kb/doc/d-guide");
    const rel = screen.getByRole("link", { name: /Changelog/ });
    expect(rel.getAttribute("href")).toContain("/kb/doc/d-rel");
  });

  it("embeds an AI chat with no collection picker (locked to the Help collection)", async () => {
    renderPage(helpClient());
    // The chat composer renders once the Help collection id resolves…
    await screen.findByPlaceholderText("Ask the knowledge base…");
    // …and it carries no collection picker (it's locked to the Help collection).
    expect(screen.queryByText("Search in")).not.toBeInTheDocument();
  });
});
