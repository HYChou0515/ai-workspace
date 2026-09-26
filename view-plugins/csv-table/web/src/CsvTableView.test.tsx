// @vitest-environment happy-dom
/**
 * The worked example from `docs/view-kind-authoring.md`, exercised the way a
 * user meets it: a `.ai.yaml` file in a workspace (#698 P5).
 *
 * This is what stops the guide rotting — the doc's snippet IS `CsvTableView`,
 * and `./index` is the same registration the doc tells a plugin author to write.
 * Runs in the HOST's vitest (it mounts the host's container and providers).
 * The item here has NO entity types, which is rca's situation: an app with no
 * `.entity/` must still be able to use a plug-in view.
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../../../../web/src/api/fileService";
import { EditModeProvider } from "../../../../web/src/hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../../../../web/src/hooks/fileBuffer";
import { MarkingProvider } from "../../../../web/src/hooks/useMarking";
import { WorkspaceSlugProvider } from "../../../../web/src/hooks/useWorkspaceSlug";
import { MarkingStore } from "../../../../web/src/lib/markings";
import { QueryWrap } from "../../../../web/src/test/queryWrapper";

const mock = vi.hoisted(() => ({
  catalog: vi.fn(),
  list: vi.fn(),
  health: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
}));
vi.mock("../../../../web/src/api/entities", () => ({ entitiesApi: mock }));

import { AiYamlRenderer } from "../../../../web/src/renderers/entity/AiYamlRenderer";
// The plugin's entry — the module the SPA `import()`s at runtime. Importing it
// here registers `csv-table` through the SAME `@aiws/view-sdk` (aliased to the
// host's barrel in the host's vitest config) the built plugin resolves.
import "./index";

function renderView(path: string, files: Record<string, string>, markings = new MarkingStore()) {
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
              <MarkingProvider store={markings}>
                <AiYamlRenderer path={path} />
              </MarkingProvider>
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
  localStorage.clear();
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

  it("takes the pane's free height, so its grid scrolls inside the pane (#847/#848 PR 5 P24)", async () => {
    // At 390 wide the grid grew to all its rows (775 px in a 356 px pane):
    // its sideways scrollbar sat below the pane, and the columns past the
    // pane's edge read as cut off. The view grows into the panel from nothing
    // (at least a few rows), and the grid, its flex child, scrolls both ways.
    mock.catalog.mockResolvedValue({ types: [], diagnostics: [] });
    renderView("/views/yield.ai.yaml", {
      "/views/yield.ai.yaml": VIEW,
      "/data/wafer.csv": "lot,yield\nA1,0.97\nB2,0.91\n",
    });
    const grid = (await screen.findByText("A1")).closest("table")!.parentElement!;
    expect(grid.style.overflow).toBe("auto");
    expect(grid.style.minHeight).toBe("0");
    const view = grid.parentElement!;
    expect(view.parentElement).toHaveClass("ev-panel");
    expect(view.style.flex).toBe("1 1 0px");
    expect(view.style.minHeight).toBe("6rem");
    expect(view.style.display).toBe("flex");
    expect(view.style.flexDirection).toBe("column");
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

// ── #847/#848 PR 5 P1: a csv-table follows a named marking, as a chart does ──

const LOTS_CSV = "lot,yield,day\nA1,0.97,2024-01-01\nB2,0.90,2024-01-02\nC3,0.5,2024-01-02\n";
const ON_FAIL = "view: csv-table\nsource: /data/lots.csv\nmarking: fail\n";

/** A body row's first data cell (past a selection checkbox, when there is one). */
function lotOf(tr: HTMLElement): string {
  return within(tr).getAllByRole("cell").find((td) => !td.querySelector("input"))!.textContent ?? "";
}

/** The first data cell of each body row the grid shows, in order. */
function shownLots(): string[] {
  return screen
    .getAllByRole("row")
    .filter((tr) => tr.closest("tbody"))
    .map(lotOf);
}

function renderLots(markings: MarkingStore, view = ON_FAIL) {
  mock.catalog.mockResolvedValue({ types: [], diagnostics: [] });
  return renderView("/views/lots.ai.yaml", { "/views/lots.ai.yaml": view, "/data/lots.csv": LOTS_CSV }, markings);
}

describe("a csv-table on a marking", () => {
  it("shows only the rows the marking lights, under a bar that says so", async () => {
    const markings = new MarkingStore();
    markings.set("fail", { lot: new Set(["B2", "C3"]) }, "/views/chart.ai.yaml");
    renderLots(markings);
    expect(await screen.findByText("B2")).toBeInTheDocument();
    expect(shownLots()).toEqual(["B2", "C3"]);
    const bar = screen.getByRole("status", { name: /marking filter/i });
    expect(bar).toHaveTextContent("filtered by fail · 2 of 3 rows");
  });

  it("names the columns the marking marks by after its count (P27)", async () => {
    const markings = new MarkingStore();
    markings.set("fail", { lot: new Set(["B2", "C3"]), day: new Set(["2024-01-02"]) }, "/views/chart.ai.yaml");
    renderLots(markings);
    expect(await screen.findByText("B2")).toBeInTheDocument();
    const bar = screen.getByRole("status", { name: /marking filter/i });
    expect(bar).toHaveTextContent("filtered by fail · 2 of 3 rows · by lot, day · show all");
  });

  it("reads a number cell as pandas and the chart do: 0.90 is the chart's 0.9", async () => {
    const markings = new MarkingStore();
    markings.set("fail", { yield: new Set(["0.9"]) }, "/views/chart.ai.yaml");
    renderLots(markings);
    expect(await screen.findByText("B2")).toBeInTheDocument();
    expect(shownLots()).toEqual(["B2"]);
  });

  it("answers a later write, and shows every row again when it is cleared", async () => {
    const markings = new MarkingStore();
    renderLots(markings);
    expect(await screen.findByText("B2")).toBeInTheDocument();
    expect(shownLots()).toEqual(["A1", "B2", "C3"]);
    expect(screen.queryByRole("status", { name: /marking filter/i })).not.toBeInTheDocument();
    act(() => markings.set("fail", { day: new Set(["2024-01-02"]) }, "/views/chart.ai.yaml"));
    expect(shownLots()).toEqual(["B2", "C3"]);
    act(() => markings.set("fail", null, "/views/chart.ai.yaml"));
    expect(shownLots()).toEqual(["A1", "B2", "C3"]);
  });

  it("'show all' keeps every row and highlights the lit ones", async () => {
    const markings = new MarkingStore();
    markings.set("fail", { lot: new Set(["B2"]) }, "/views/chart.ai.yaml");
    renderLots(markings);
    fireEvent.click(await screen.findByRole("button", { name: "show all" }));
    expect(shownLots()).toEqual(["A1", "B2", "C3"]);
    const marked = screen.getAllByRole("row").filter((tr) => tr.hasAttribute("data-marked"));
    expect(marked.map(lotOf)).toEqual(["B2"]);
  });

  it("shows every row and says so when it shares no column with the marking", async () => {
    const markings = new MarkingStore();
    markings.set("fail", { wafer: new Set(["W1"]) }, "/views/chart.ai.yaml");
    renderLots(markings);
    expect(await screen.findByText("B2")).toBeInTheDocument();
    expect(shownLots()).toEqual(["A1", "B2", "C3"]);
    expect(screen.getByRole("status", { name: /marking filter/i })).toHaveTextContent("no column in common with fail");
  });

  it("carries the header's marking control on a view whose file names no marking", async () => {
    const markings = new MarkingStore();
    markings.set("fail", { lot: new Set(["A1"]) }, "/views/chart.ai.yaml");
    renderLots(markings, "view: csv-table\nsource: /data/lots.csv\n");
    expect(await screen.findByText("B2")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: /marking/i }), { target: { value: "fail" } });
    expect(shownLots()).toEqual(["A1"]);
  });
});

// ── P2: selecting rows in a csv-table writes its marking ────────────────────

function held(markings: MarkingStore): Record<string, string[]> | undefined {
  const entry = markings.get("fail");
  if (!entry) return undefined;
  return Object.fromEntries(Object.entries(entry.marking).map(([k, v]) => [k, [...v].sort()]));
}

describe("selecting rows in a csv-table on a marking", () => {
  it("writes the selected rows' `keys:` values, with this view as the source", async () => {
    const markings = new MarkingStore();
    renderLots(markings, `${ON_FAIL}keys: [lot]\n`);
    fireEvent.click(await screen.findByRole("checkbox", { name: "select row 2" }));
    expect(held(markings)).toEqual({ lot: ["B2"] });
    expect(markings.get("fail")!.source).toBe("/views/lots.ai.yaml");
    // the table it was made in keeps every row, the selected one checked
    expect(shownLots()).toEqual(["A1", "B2", "C3"]);
    expect(screen.getByRole("checkbox", { name: "select row 2" })).toBeChecked();
  });

  it("writes a number cell as the chart writes it: 0.90 goes in as 0.9", async () => {
    const markings = new MarkingStore();
    renderLots(markings, `${ON_FAIL}keys: [yield]\n`);
    fireEvent.click(await screen.findByRole("checkbox", { name: "select row 2" }));
    expect(held(markings)).toEqual({ yield: ["0.9"] });
  });

  it("without `keys:`, writes the columns the marking already holds", async () => {
    const markings = new MarkingStore();
    markings.set("fail", { lot: new Set(["A1"]) }, "/views/chart.ai.yaml");
    renderLots(markings);
    fireEvent.click(await screen.findByRole("button", { name: "show all" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "select row 3" }));
    expect(held(markings)).toEqual({ lot: ["A1", "C3"] });
  });

  it("select all marks every row shown, and again clears the marking", async () => {
    const markings = new MarkingStore();
    renderLots(markings, `${ON_FAIL}keys: [lot]\n`);
    fireEvent.click(await screen.findByRole("checkbox", { name: "select all rows" }));
    expect(held(markings)).toEqual({ lot: ["A1", "B2", "C3"] });
    fireEvent.click(screen.getByRole("checkbox", { name: "select all rows" }));
    expect(markings.get("fail")).toBeUndefined();
  });

  it("writes nothing, and says why in the header, with no keys and an empty marking", async () => {
    const markings = new MarkingStore();
    renderLots(markings);
    const box = await screen.findByRole("checkbox", { name: "select row 1" });
    expect(box).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent(/selecting rows marks nothing/i);
    fireEvent.click(box);
    expect(markings.get("fail")).toBeUndefined();
  });

  it("offers no selection at all on no marking — the grid is as it was", async () => {
    const markings = new MarkingStore();
    renderLots(markings, "view: csv-table\nsource: /data/lots.csv\n");
    expect(await screen.findByText("B2")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
