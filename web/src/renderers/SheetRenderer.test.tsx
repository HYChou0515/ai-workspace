// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../api/fileService";
import { EditModeProvider, useEditMode } from "../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../hooks/fileBuffer";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import { QueryWrap } from "../test/queryWrapper";
import { SheetRenderer } from "./SheetRenderer";

/** Flip the shared edit-mode on for a path through the real API, so the test
 * drives the same switch the Edit button does. */
function EnableEdit({ path }: { path: string }) {
  const { toggle } = useEditMode();
  useEffect(() => {
    toggle(path);
  }, [toggle, path]);
  return null;
}

function storeWith(text: string, path: string): FileBufferStore {
  return new FileBufferStore({
    readFile: vi.fn(async () => ({
      kind: "text" as const,
      path,
      size: text.length,
      text,
      encoding: "utf-8" as const,
    })),
    writeFile: vi.fn(async () => {}),
  });
}

async function renderSheet(text: string, path = "/data/x.ai.csv") {
  const store = storeWith(text, path);
  store.ensureLoaded(path);
  await new Promise((r) => setTimeout(r, 0));
  render(
    <QueryWrap>
      <WorkspaceSlugProvider value="pm">
        <FileServiceProvider value={investigationFileService("pm", "item1")}>
          <EditModeProvider>
            <FileBufferProvider store={store}>
              <SheetRenderer path={path} />
            </FileBufferProvider>
          </EditModeProvider>
        </FileServiceProvider>
      </WorkspaceSlugProvider>
    </QueryWrap>,
  );
  return { store, path };
}

describe("SheetRenderer", () => {
  afterEach(cleanup);

  it("writes a typed cell back through the file buffer", async () => {
    const { store, path } = await renderSheet("wafer,qty\nW01,120\n");

    const cell = await screen.findByLabelText("R1C2"); // the 120
    await userEvent.clear(cell);
    await userEvent.type(cell, "130{Enter}");

    expect(store.snapshot(path).text).toBe("wafer,qty\nW01,130\n");
  });

  it("#205: a `.readonly/` path renders cells that cannot be typed in", async () => {
    const { store, path } = await renderSheet("wafer,qty\nW01,120\n", "/.readonly/snapshot.ai.csv");

    const cell = await screen.findByLabelText("R1C2");
    expect(cell).toHaveAttribute("readonly");

    await userEvent.type(cell, "999");
    expect(store.snapshot(path).text).toBe("wafer,qty\nW01,120\n");
  });

  it("moves down on Enter and up on Shift+Enter", async () => {
    await renderSheet("wafer,qty\nW01,120\nW02,98\n");

    await userEvent.click(await screen.findByLabelText("Column 1 name"));
    await userEvent.keyboard("{Enter}");
    expect(screen.getByLabelText("R1C1")).toHaveFocus();

    await userEvent.keyboard("{Shift>}{Enter}{/Shift}");
    expect(screen.getByLabelText("Column 1 name")).toHaveFocus();
  });

  it("moves right on Tab and left on Shift+Tab", async () => {
    await renderSheet("wafer,qty\nW01,120\n");

    await userEvent.click(await screen.findByLabelText("R1C1"));
    await userEvent.tab();
    expect(screen.getByLabelText("R1C2")).toHaveFocus();

    await userEvent.tab({ shift: true });
    expect(screen.getByLabelText("R1C1")).toHaveFocus();
  });

  it("writes a structural edit back to the file, keeping its CRLF line endings", async () => {
    const { store, path } = await renderSheet("wafer,qty\r\nW01,120\r\n");

    await userEvent.click(await screen.findByLabelText("R1C1"));
    await userEvent.click(screen.getByRole("button", { name: "Insert row below" }));

    expect(store.snapshot(path).text).toBe("wafer,qty\r\nW01,120\r\n,\r\n");
  });

  it("hands over to the byte editor when Edit is toggled on", async () => {
    // The registry marks this type `editToggle`, and the plan leans on the byte
    // editor being the escape hatch for a file the grid can't help with. If the
    // renderer ignores the toggle, pressing Edit does nothing at all.
    const path = "/data/x.ai.csv";
    const store = storeWith("wafer,qty\nW01,120\n", path);
    store.ensureLoaded(path);
    await new Promise((r) => setTimeout(r, 0));
    render(
      <QueryWrap>
        <WorkspaceSlugProvider value="pm">
          <FileServiceProvider value={investigationFileService("pm", "item1")}>
            <EditModeProvider>
              <EnableEdit path={path} />
              <FileBufferProvider store={store}>
                <SheetRenderer path={path} />
              </FileBufferProvider>
            </EditModeProvider>
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>,
    );

    expect(screen.queryByLabelText("Column 1 name")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Insert row below" })).not.toBeInTheDocument();
  });

  it("falls back to the byte editor when the file is not decodable text", async () => {
    // A grid over mojibake is worse than useless: it invites edits that would
    // re-encode the bytes. The byte editor is the honest escape hatch.
    const path = "/data/blob.ai.csv";
    const store = new FileBufferStore({
      readFile: vi.fn(async () => ({
        kind: "binary" as const,
        path,
        size: 4,
        text: "\u0000\u0001",
        encoding: "binary" as const,
      })),
      writeFile: vi.fn(async () => {}),
    });
    store.ensureLoaded(path);
    await new Promise((r) => setTimeout(r, 0));
    render(
      <QueryWrap>
        <WorkspaceSlugProvider value="pm">
          <FileServiceProvider value={investigationFileService("pm", "item1")}>
            <EditModeProvider>
              <FileBufferProvider store={store}>
                <SheetRenderer path={path} />
              </FileBufferProvider>
            </EditModeProvider>
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>,
    );

    expect(screen.queryByLabelText("Column 1 name")).not.toBeInTheDocument();
    expect(await screen.findByText(/not text/i)).toBeInTheDocument();
  });

  it("discards an edit on Esc, leaving the file clean", async () => {
    const { store, path } = await renderSheet("wafer,qty\nW01,120\n");

    const cell = await screen.findByLabelText("R1C2");
    await userEvent.clear(cell);
    await userEvent.type(cell, "999{Escape}");

    expect(store.snapshot(path).text).toBe("wafer,qty\nW01,120\n");
    expect(store.isDirty(path)).toBe(false);
    expect(cell).toHaveValue("120");
  });
});
