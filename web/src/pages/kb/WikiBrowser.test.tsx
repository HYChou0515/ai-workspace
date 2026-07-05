// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetKbMock, _seedWikiMock, _setWikiStatusMock, mockKbApi } from "../../api/kbMock";
import { QueryWrap } from "../../test/queryWrapper";
import { WikiBrowser } from "./WikiBrowser";

// WikiBrowser renders the URL-driven KbWikiIde (#93), so mount it under the
// wiki splat route; the empty/building states don't read the URL but the
// wrapper is harmless there.
const render = (ui: ReactElement, start = "/kb/collections/c/wiki") =>
  rtlRender(
    <MemoryRouter initialEntries={[start]}>
      <Routes>
        <Route path="/kb/collections/:cid/wiki/*" element={ui} />
      </Routes>
    </MemoryRouter>,
    { wrapper: QueryWrap },
  );

describe("WikiBrowser (#50 P7)", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("opens the index and follows a [[wikilink]] to another page", async () => {
    _seedWikiMock("col-1", {
      "/index.md": "# Wiki\n\nSee [[reflow]].\n",
      "/entities/reflow.md": "# Reflow\n\nZone 3 at 245C.\n\nSources: reflow.md\n",
    });
    render(<WikiBrowser collectionId="col-1" client={mockKbApi} />);

    const link = await screen.findByRole("button", { name: "reflow" });
    await userEvent.click(link);
    expect(await screen.findByText(/Zone 3 at 245C/)).toBeInTheDocument();
  });

  it("renders the wiki pages as a file tree (real path names, not a grouped nav)", async () => {
    _seedWikiMock("col-2", {
      "/index.md": "# Wiki\n",
      "/entities/reflow-zone-3.md": "# Reflow Zone 3\n",
      "/concepts/voiding.md": "# Voiding\n",
    });
    render(<WikiBrowser collectionId="col-2" client={mockKbApi} />);

    // folders + files by their real path names (the shared FileTree shell)
    expect(await screen.findByText("entities")).toBeInTheDocument();
    expect(screen.getByText("concepts")).toBeInTheDocument();
    expect(screen.getByText("reflow-zone-3.md")).toBeInTheDocument();
  });

  it("truncates a long collection name in the header but keeps the full name as a tooltip (#445)", async () => {
    _seedWikiMock("col-long", { "/index.md": "# Wiki\n" });
    const longName =
      "A Very Long Collection Name That Would Otherwise Overflow The Wiki Header Bar";
    render(<WikiBrowser collectionId="col-long" collectionName={longName} client={mockKbApi} />);

    // The full name survives as a hover tooltip even when the cell ellipsizes.
    const nameEl = await screen.findByTitle(longName);
    expect(nameEl).toHaveTextContent(longName);
    expect(nameEl).toHaveStyle({
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    });
  });

  it("renders a clickable Sources footer that opens the source document", async () => {
    _seedWikiMock("col-3", {
      "/index.md": "# Wiki\n\nBody.\n\nSources: reflow.md\n",
    });
    // Put a document at that path so the footer chip resolves to a doc id.
    const file = new File(["zone 3"], "reflow.md", { type: "text/markdown" });
    await mockKbApi.uploadDocument("col-3", file);

    const onOpenDoc = vi.fn();
    render(<WikiBrowser collectionId="col-3" onOpenDoc={onOpenDoc} client={mockKbApi} />);

    // The chip's accessible name is the filename; its title is "Open …".
    const chip = await screen.findByRole("button", { name: /reflow\.md/ });
    expect(chip).toHaveAttribute("title", "Open reflow.md");
    await userEvent.click(chip);
    expect(onOpenDoc).toHaveBeenCalledWith("col-3/me/reflow.md");
  });

  it("ignores .gitkeep placeholder files in the page tree (#79)", async () => {
    _seedWikiMock("col-gk", {
      "/index.md": "# Wiki\n",
      "/.gitkeep": "",
      "/entities/.gitkeep": "",
      "/entities/reflow.md": "# Reflow\n",
    });
    render(<WikiBrowser collectionId="col-gk" client={mockKbApi} />);

    // the real page shows in the tree…
    expect(await screen.findByText("reflow.md")).toBeInTheDocument();
    // …but the .gitkeep placeholders never appear as pages
    expect(screen.queryByText(/gitkeep/i)).not.toBeInTheDocument();
  });

  it("shows an empty state with a build action when there's no wiki yet", async () => {
    render(<WikiBrowser collectionId="col-empty" client={mockKbApi} />);
    expect(await screen.findByText(/hasn't been built yet/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Build the wiki/i })).toBeInTheDocument();
  });

  it("explains a build that produced nothing (e.g. hit the step limit)", async () => {
    // No pages + a finished build with errors → the empty state must say why.
    _setWikiStatusMock("col-err", {
      building: false,
      total: 28,
      done: 28,
      errors: 28,
      last_error: "hit the step limit (10 turns) before finishing",
    });
    render(<WikiBrowser collectionId="col-err" client={mockKbApi} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/step limit/i);
  });

  it("edits per-collection wiki guidance from the empty state and saves it (#90)", async () => {
    const spy = vi.spyOn(mockKbApi, "updateCollection");
    // col-g has no pages → empty state, which must STILL expose the editor so
    // guidance can be set BEFORE the first build (no death-lock).
    render(
      <WikiBrowser
        collectionId="col-g"
        client={mockKbApi}
        maintainerGuidance="Organize by zone."
        readerGuidance=""
      />,
    );

    const writing = await screen.findByLabelText(/writing guidance/i);
    expect(writing).toHaveValue("Organize by zone."); // prefilled from props
    const answering = screen.getByLabelText(/answering guidance/i);

    await userEvent.clear(writing);
    await userEvent.type(writing, "Group by defect code.");
    await userEvent.type(answering, "Lead with a one-line summary.");
    await userEvent.click(screen.getByRole("button", { name: /save guidance/i }));

    expect(spy).toHaveBeenCalledWith("col-g", {
      wiki_maintainer_guidance: "Group by defect code.",
      wiki_reader_guidance: "Lead with a one-line summary.",
    });
  });

  it("shows the live building panel with the current phase and source counter", async () => {
    _setWikiStatusMock("col-b", { building: true, total: 24, done: 3, phase: "writing" });
    render(<WikiBrowser collectionId="col-b" client={mockKbApi} />);

    expect(await screen.findByText(/Updating the wiki/i)).toBeInTheDocument();
    expect(screen.getByText("Writing pages")).toBeInTheDocument();
    expect(screen.getByText("3 / 24")).toBeInTheDocument(); // real source-level progress
  });

  it("keeps already-built pages browsable during a rebuild (slim indicator, no takeover)", async () => {
    _seedWikiMock("col-rb", {
      "/index.md": "# Wiki\n",
      "/entities/reflow.md": "# Reflow\n\nZone 3 at 245C.\n",
    });
    _setWikiStatusMock("col-rb", { building: true, total: 24, done: 3, phase: "writing" });
    render(<WikiBrowser collectionId="col-rb" client={mockKbApi} />);

    // the done pages are still browsable in the tree …
    expect(await screen.findByText("reflow.md")).toBeInTheDocument();
    // … and the build shows as a COMPACT single line that still carries the
    // live info (phase + counter) — shrunk, not omitted — not the takeover panel
    const pill = screen.getByTestId("wiki-building");
    expect(pill).toHaveTextContent("Writing pages");
    expect(pill).toHaveTextContent("3 / 24");
    expect(screen.queryByText(/Updating the wiki/i)).not.toBeInTheDocument();
  });

  it("labels the wiki as AI-written and editable (#173)", async () => {
    _seedWikiMock("col-badge", { "/index.md": "# Wiki\n" });
    render(<WikiBrowser collectionId="col-badge" client={mockKbApi} />);

    expect(await screen.findByText("AI 撰寫，可編輯")).toBeInTheDocument();
    // the old jargon that didn't convey "you can edit this" is gone
    expect(screen.queryByText("AI-maintained")).not.toBeInTheDocument();
  });

  it("asks for confirmation before a rebuild, warning about manual edits (#173)", async () => {
    _seedWikiMock("col-rbc", { "/index.md": "# Wiki\n" });
    const rebuild = vi.spyOn(mockKbApi, "rebuildWiki");
    render(<WikiBrowser collectionId="col-rbc" client={mockKbApi} />);

    // Clicking Rebuild asks first — it does NOT immediately refresh.
    await userEvent.click(await screen.findByRole("button", { name: /Rebuild/i }));
    expect(rebuild).not.toHaveBeenCalled();
    expect(screen.getByText(/可能改寫你手動編輯過的頁面/)).toBeInTheDocument();

    // Cancel backs out without rebuilding.
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(rebuild).not.toHaveBeenCalled();

    // Confirming actually rebuilds.
    await userEvent.click(await screen.findByRole("button", { name: /Rebuild/i }));
    await userEvent.click(screen.getByRole("button", { name: "重建" }));
    expect(rebuild).toHaveBeenCalledWith("col-rbc");
  });

  // ── #281: code-wiki build phases + a stale-after-edit hint ──────────────

  it("shows the code-wiki build's own phases during a first build (#281)", async () => {
    // A code wiki builds via summarise-files → assemble pages, not the prose
    // reading/identifying/writing pipeline; its phase label must be meaningful.
    _setWikiStatusMock("col-code", { building: true, total: 2, done: 1, phase: "cards" });
    render(<WikiBrowser collectionId="col-code" client={mockKbApi} />);

    expect(await screen.findByText(/Updating the wiki/i)).toBeInTheDocument();
    // shown in the step list (and the header pill) — at least once
    expect(screen.getAllByText(/Summarising source files/i).length).toBeGreaterThan(0);
    // the prose-only first step must NOT be shown for a code build
    expect(screen.queryByText("Reading documents")).not.toBeInTheDocument();
  });

  it("labels the compact pill with the code-wiki phase during a rebuild (#281)", async () => {
    _seedWikiMock("col-codepill", { "/index.md": "# Wiki\n" });
    _setWikiStatusMock("col-codepill", {
      building: true,
      total: 3,
      done: 2,
      phase: "finalizing",
    });
    render(<WikiBrowser collectionId="col-codepill" client={mockKbApi} />);

    const pill = await screen.findByTestId("wiki-building");
    expect(pill).toHaveTextContent(/Assembling/i);
  });

  it("hints that a code wiki refreshes only on rebuild (#281 Q4)", async () => {
    _seedWikiMock("col-codehint", { "/index.md": "# Wiki\n" });
    render(<WikiBrowser collectionId="col-codehint" client={mockKbApi} isCodeWiki />);

    expect(await screen.findByText(/appear after the next rebuild/i)).toBeInTheDocument();
  });

  it("shows no rebuild hint for a prose wiki (#281 Q4)", async () => {
    _seedWikiMock("col-prose", { "/index.md": "# Wiki\n" });
    render(<WikiBrowser collectionId="col-prose" client={mockKbApi} />);

    await screen.findByText(/AI 撰寫/);
    expect(screen.queryByText(/appear after the next rebuild/i)).not.toBeInTheDocument();
  });
});
