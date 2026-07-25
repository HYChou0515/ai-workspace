// @vitest-environment happy-dom
// The member-permission gate (#455 §E) lives in its own file because it needs
// `useItemCanWrite` stubbed for the whole module — the real hook resolves the
// App item through the resource queries, which is a different concern from
// whether the grid honours the answer.
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../api/fileService";
import { EditModeProvider } from "../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../hooks/fileBuffer";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import { QueryWrap } from "../test/queryWrapper";

vi.mock("../hooks/useItemCanWrite", () => ({ useItemCanWrite: () => false }));

import { SheetRenderer } from "./SheetRenderer";

const PATH = "/data/x.ai.csv";
const TEXT = "wafer,qty\nW01,120\n";

async function renderReadOnlyMember() {
  const store = new FileBufferStore({
    readFile: vi.fn(async () => ({
      kind: "text" as const,
      path: PATH,
      size: TEXT.length,
      text: TEXT,
      encoding: "utf-8" as const,
    })),
    writeFile: vi.fn(async () => {}),
  });
  store.ensureLoaded(PATH);
  await new Promise((r) => setTimeout(r, 0));
  render(
    <QueryWrap>
      <WorkspaceSlugProvider value="pm">
        <FileServiceProvider value={investigationFileService("pm", "item1")}>
          <EditModeProvider>
            <FileBufferProvider store={store}>
              <SheetRenderer path={PATH} />
            </FileBufferProvider>
          </EditModeProvider>
        </FileServiceProvider>
      </WorkspaceSlugProvider>
    </QueryWrap>,
  );
  return store;
}

describe("SheetRenderer — read-only member (#455 §E)", () => {
  afterEach(cleanup);

  it("cannot type into a cell, and the file stays untouched", async () => {
    const store = await renderReadOnlyMember();

    const cell = await screen.findByLabelText("R2C2");
    expect(cell).toHaveAttribute("readonly");

    await userEvent.type(cell, "999");
    expect(store.snapshot(PATH).text).toBe(TEXT);
    expect(store.isDirty(PATH)).toBe(false);
  });
});
