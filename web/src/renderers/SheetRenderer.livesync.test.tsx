// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../api/fileService";
import { EditModeProvider } from "../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../hooks/fileBuffer";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import { QueryWrap } from "../test/queryWrapper";

// One `file_changed` on the item's broadcast stream, then idle.
const stream = vi.hoisted(() => ({ emit: null as null | (() => void) }));
vi.mock("../api", () => ({
  api: {
    subscribeInvestigation: async function* () {
      yield await new Promise<{ type: string }>((resolve) => {
        stream.emit = () => resolve({ type: "file_changed" });
      });
      await new Promise(() => {}); // stay open
    },
  },
}));

import { SheetRenderer } from "./SheetRenderer";

const PATH = "/data/x.ai.csv";
const TEXT = "wafer,qty\nW01,120\n";

async function renderSheet() {
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

describe("SheetRenderer — the file changed outside the editor", () => {
  afterEach(cleanup);

  it("says so and lets the user choose, instead of merging or discarding silently", async () => {
    const store = await renderSheet();

    // An unsaved edit is in flight.
    const cell = await screen.findByLabelText("R1C2");
    await userEvent.dblClick(cell); // a click selects; editing needs the second one
    await userEvent.clear(cell);
    await userEvent.type(cell, "130{Enter}");
    expect(store.isDirty(PATH)).toBe(true);

    await waitFor(() => expect(stream.emit).not.toBeNull());
    stream.emit?.();

    // The unsaved edit is still there, and the user is told.
    expect(await screen.findByText(/changed outside/i)).toBeInTheDocument();
    expect(screen.getByLabelText("R1C2")).toHaveValue("130");
    expect(screen.getByRole("button", { name: /keep my changes/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reload/i })).toBeInTheDocument();
  });
});
