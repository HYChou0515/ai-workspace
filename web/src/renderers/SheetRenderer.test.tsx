// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../api/fileService";
import { EditModeProvider } from "../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../hooks/fileBuffer";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import { QueryWrap } from "../test/queryWrapper";
import { SheetRenderer } from "./SheetRenderer";

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

    const cell = await screen.findByLabelText("R2C2"); // the 120
    await userEvent.clear(cell);
    await userEvent.type(cell, "130{Enter}");

    expect(store.snapshot(path).text).toBe("wafer,qty\nW01,130\n");
  });

  it("#205: a `.readonly/` path renders cells that cannot be typed in", async () => {
    const { store, path } = await renderSheet("wafer,qty\nW01,120\n", "/.readonly/snapshot.ai.csv");

    const cell = await screen.findByLabelText("R2C2");
    expect(cell).toHaveAttribute("readonly");

    await userEvent.type(cell, "999");
    expect(store.snapshot(path).text).toBe("wafer,qty\nW01,120\n");
  });

  it("moves down on Enter and up on Shift+Enter", async () => {
    await renderSheet("wafer,qty\nW01,120\nW02,98\n");

    await userEvent.click(await screen.findByLabelText("R1C1"));
    await userEvent.keyboard("{Enter}");
    expect(screen.getByLabelText("R2C1")).toHaveFocus();

    await userEvent.keyboard("{Shift>}{Enter}{/Shift}");
    expect(screen.getByLabelText("R1C1")).toHaveFocus();
  });

  it("moves right on Tab and left on Shift+Tab", async () => {
    await renderSheet("wafer,qty\nW01,120\n");

    await userEvent.click(await screen.findByLabelText("R2C1"));
    await userEvent.tab();
    expect(screen.getByLabelText("R2C2")).toHaveFocus();

    await userEvent.tab({ shift: true });
    expect(screen.getByLabelText("R2C1")).toHaveFocus();
  });

  it("discards an edit on Esc, leaving the file clean", async () => {
    const { store, path } = await renderSheet("wafer,qty\nW01,120\n");

    const cell = await screen.findByLabelText("R2C2");
    await userEvent.clear(cell);
    await userEvent.type(cell, "999{Escape}");

    expect(store.snapshot(path).text).toBe("wafer,qty\nW01,120\n");
    expect(store.isDirty(path)).toBe(false);
    expect(cell).toHaveValue("120");
  });
});
