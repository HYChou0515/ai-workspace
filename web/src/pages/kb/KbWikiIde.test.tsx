// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { _resetKbMock, _seedWikiMock, mockKbApi } from "../../api/kbMock";
import { renderWithQuery as renderQ } from "../../test/queryWrapper";
import { KbWikiIde } from "./KbWikiIde";

// KbWikiIde is URL-driven (#93): the open page is the `wiki/*` splat. Mount it
// under that route so clicking a page navigates and the same route re-renders.
function renderWithQuery(ui: ReactElement, start = "/kb/collections/c/wiki") {
  return renderQ(
    <MemoryRouter initialEntries={[start]}>
      <Routes>
        <Route path="/kb/collections/:cid/wiki/*" element={ui} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("KbWikiIde (#D editable wiki)", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("shows the pages as an editable tree but offers NO upload (the wiki is authored)", async () => {
    _seedWikiMock("c1", { "/index.md": "# Wiki\n", "/entities/reflow.md": "# Reflow\n" });
    renderWithQuery(<KbWikiIde collectionId="c1" client={mockKbApi} />);

    // the file tree shows the page + its folder …
    expect(await screen.findByText("index.md")).toBeInTheDocument();
    expect(screen.getByText("entities")).toBeInTheDocument();
    // … editing affordances are present (create) …
    expect(screen.getByTitle(/new file/i)).toBeInTheDocument();
    // … but there's no upload control (caps.upload = false)
    expect(screen.queryByTitle(/upload/i)).not.toBeInTheDocument();
  });

  it("auto-opens the index page and renders it (preview)", async () => {
    _seedWikiMock("c2", { "/index.md": "# Welcome\n\nThe home page.\n" });
    renderWithQuery(<KbWikiIde collectionId="c2" client={mockKbApi} />);
    expect(await screen.findByRole("heading", { name: "Welcome" })).toBeInTheDocument();
    expect(screen.getByText(/The home page/)).toBeInTheDocument();
  });

  it("deep-links straight to a wiki page through the splat (#93)", async () => {
    _seedWikiMock("c4", {
      "/index.md": "# Home\n",
      "/entities/reflow.md": "# Reflow\n\nZone 3 at 245C.\n",
    });
    renderWithQuery(
      <KbWikiIde collectionId="c4" client={mockKbApi} />,
      "/kb/collections/c4/wiki/entities/reflow.md",
    );
    // the page named by the URL opens directly, not the index fallback
    expect(await screen.findByText(/Zone 3 at 245C/)).toBeInTheDocument();
  });

  it("creates a new page in a folder via the tree (write, no upload route)", async () => {
    const user = userEvent.setup();
    _seedWikiMock("c3", { "/entities/reflow.md": "# Reflow\n" });
    renderWithQuery(<KbWikiIde collectionId="c3" client={mockKbApi} />);
    await user.click(await screen.findByText("entities"));
    await user.click(screen.getByTitle(/new file/i));
    await user.type(await screen.findByPlaceholderText("file name"), "voiding.md{Enter}");
    // the new page lands under the folder and becomes a wiki page
    const pages = (await mockKbApi.listWikiPages("c3")).pages;
    expect(pages).toContain("/entities/voiding.md");
  });
});
