// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { _resetKbMock, _seedWikiMock, mockKbApi } from "../../api/kbMock";
import { renderWithQuery } from "../../test/queryWrapper";
import { KbWikiIde } from "./KbWikiIde";

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
