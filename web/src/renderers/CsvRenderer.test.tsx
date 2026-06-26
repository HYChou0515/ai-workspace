// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EditModeProvider } from "../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../hooks/fileBuffer";
import { CsvRenderer } from "./CsvRenderer";

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

async function renderLoaded(text: string, path: string) {
  const store = storeWith(text, path);
  store.ensureLoaded(path);
  await new Promise((r) => setTimeout(r, 0));
  return render(
    <EditModeProvider>
      <FileBufferProvider store={store}>
        <CsvRenderer path={path} />
      </FileBufferProvider>
    </EditModeProvider>,
  );
}

describe("CsvRenderer", () => {
  afterEach(cleanup);

  it("renders a comma file as a multi-column table", async () => {
    await renderLoaded("a,b\n1,2\n", "/data/x.csv");
    expect(await screen.findByRole("columnheader", { name: "a" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "b" })).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("#255: renders a .tsv file as a table by splitting on tabs", async () => {
    await renderLoaded("a\tb\n1\t2\n", "/data/x.tsv");
    expect(await screen.findByRole("columnheader", { name: "a" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "b" })).toBeInTheDocument();
    // Two real columns — not a single cell holding "a\tb".
    expect(screen.getAllByRole("columnheader")).toHaveLength(2);
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});
