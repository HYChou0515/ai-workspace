// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../api/fileService";
import type { FileContent } from "../api/types";
import { EditModeProvider } from "../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../hooks/fileBuffer";
import { QueryWrap } from "../test/queryWrapper";

// The sheet pane asks whether the member may write, which the real hook resolves
// through the resource queries — irrelevant here, and a live fetch in a unit test.
vi.mock("../hooks/useItemCanWrite", () => ({ useItemCanWrite: () => true }));
import { HtmlRenderer } from "./HtmlRenderer";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { PdfRenderer } from "./PdfRenderer";
import { SheetRenderer } from "./SheetRenderer";
import { StructuredPane } from "./structuredPane";
import { TextRenderer } from "./TextRenderer";

afterEach(cleanup);

const PATH = "/data/x.csv";

/** A store whose read never settles, so every renderer stays in its loading state. */
function pendingStore(): FileBufferStore {
  return new FileBufferStore({
    readFile: vi.fn(() => new Promise<FileContent>(() => {})),
    writeFile: vi.fn(async () => {}),
  });
}

function renderPane(node: React.ReactNode) {
  return render(
    <QueryWrap>
      <FileServiceProvider value={investigationFileService("rca", "inv")}>
        <EditModeProvider>
          <FileBufferProvider store={pendingStore()}>{node}</FileBufferProvider>
        </EditModeProvider>
      </FileServiceProvider>
    </QueryWrap>,
  );
}

/** Every pane says "Loading <path>…" before its content arrives. That line is a
 * path the user reads (and sometimes retypes) while nothing else is on screen, so
 * it speaks the relative form like the rest of the UI (#549). */
describe("renderers name the file relatively while loading", () => {
  const CASES: [string, React.ReactNode][] = [
    ["text", <TextRenderer path={PATH} />],
    ["markdown", <MarkdownRenderer path={PATH} />],
    ["html", <HtmlRenderer path={PATH} />],
    ["pdf", <PdfRenderer path={PATH} />],
    ["sheet", <SheetRenderer path={PATH} />],
    ["structured", <StructuredPane path={PATH} render={() => null} />],
  ];

  it.each(CASES)("%s", (_name, node) => {
    renderPane(node);
    expect(screen.getByText(/Loading/)).toHaveTextContent("Loading data/x.csv…");
  });
});
