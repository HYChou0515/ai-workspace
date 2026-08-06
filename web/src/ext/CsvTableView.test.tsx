// @vitest-environment happy-dom
/**
 * The worked example from `docs/view-kind-authoring.md`, exercised the way a
 * user meets it: a `.ai.yaml` file in a workspace (#698 P5).
 *
 * This is what stops the guide rotting — the doc's snippet IS `CsvTableView`,
 * and `./index` is the same registration the doc tells a maintainer to write.
 * The item here has NO entity types, which is rca's situation: an app with no
 * `.entity/` must still be able to use a plug-in view.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../api/fileService";
import { EditModeProvider } from "../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../hooks/fileBuffer";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import { QueryWrap } from "../test/queryWrapper";

const mock = vi.hoisted(() => ({
  catalog: vi.fn(),
  list: vi.fn(),
  health: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
}));
vi.mock("../api/entities", () => ({ entitiesApi: mock }));

import { AiYamlRenderer } from "../renderers/entity/AiYamlRenderer";
// The side-effect import the main program makes — this is what registers
// `csv-table`. Importing it here means the test covers the real wiring.
import "./index";

function renderView(path: string, files: Record<string, string>) {
  const store = new FileBufferStore({
    readFile: vi.fn(async (p: string) => {
      const text = files[p];
      if (text === undefined) throw new Error(`no such file: ${p}`);
      return { kind: "text" as const, path: p, size: text.length, text, encoding: "utf-8" as const };
    }),
    writeFile: vi.fn(async () => {}),
  });
  store.ensureLoaded(path);
  return render(
    <QueryWrap>
      <WorkspaceSlugProvider value="rca">
        <FileServiceProvider value={investigationFileService("rca", "item1")}>
          <EditModeProvider>
            <FileBufferProvider store={store}>
              <AiYamlRenderer path={path} />
            </FileBufferProvider>
          </EditModeProvider>
        </FileServiceProvider>
      </WorkspaceSlugProvider>
    </QueryWrap>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const VIEW = "view: csv-table\ntitle: Wafer yield\nsource: /data/wafer.csv\n";

describe("the csv-table example kind", () => {
  it("draws the CSV named by `source:` in an item that has no entity types at all", async () => {
    mock.catalog.mockResolvedValue({ types: [], diagnostics: [] });

    renderView("/views/yield.ai.yaml", {
      "/views/yield.ai.yaml": VIEW,
      "/data/wafer.csv": "lot,yield\nA1,0.97\nB2,0.91\n",
    });

    expect(await screen.findByText("A1")).toBeInTheDocument();
    expect(screen.getByText("0.91")).toBeInTheDocument();
    // the panel header comes from `title:`
    expect(screen.getByRole("heading", { name: /wafer yield/i })).toBeInTheDocument();
    // a kind with no `entity:` must not make the app fetch entity records
    expect(mock.list).not.toHaveBeenCalled();
    // nor wear entity chrome. Caught by looking at the real screen, not by a
    // unit test: the banner read "No schema for — showing raw fields", with an
    // empty entity name, above a perfectly good grid.
    expect(screen.queryByText(/no schema for/i)).not.toBeInTheDocument();
  });

  it("reads a .tsv with tab delimiters, not commas", async () => {
    mock.catalog.mockResolvedValue({ types: [], diagnostics: [] });

    renderView("/views/t.ai.yaml", {
      "/views/t.ai.yaml": "view: csv-table\nsource: /data/x.tsv\n",
      "/data/x.tsv": "lot\tyield\nA1\t0.97\n",
    });

    expect(await screen.findByText("A1")).toBeInTheDocument();
  });

  it("says what is missing when the view file names no `source:`", async () => {
    mock.catalog.mockResolvedValue({ types: [], diagnostics: [] });

    renderView("/views/bare.ai.yaml", { "/views/bare.ai.yaml": "view: csv-table\n" });

    expect(await screen.findByRole("status")).toHaveTextContent(/needs a `source:`/i);
  });

  it("surfaces a read failure instead of rendering an empty grid", async () => {
    mock.catalog.mockResolvedValue({ types: [], diagnostics: [] });

    renderView("/views/gone.ai.yaml", { "/views/gone.ai.yaml": "view: csv-table\nsource: /data/missing.csv\n" });

    expect(await screen.findByRole("status")).toHaveTextContent(/missing\.csv/);
  });
});
