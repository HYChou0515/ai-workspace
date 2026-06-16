// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetKbMock, _seedWikiMock, _setWikiStatusMock, mockKbApi } from "../../api/kbMock";
import { QueryWrap } from "../../test/queryWrapper";
import { WikiBrowser } from "./WikiBrowser";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

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
});
