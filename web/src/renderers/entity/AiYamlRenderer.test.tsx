// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../../api/fileService";
import { EditModeProvider } from "../../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../../hooks/fileBuffer";
import { type OpenFile, OpenFileProvider } from "../../hooks/openFile";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { QueryWrap } from "../../test/queryWrapper";

// Stub only the network leaf; the hooks + view components are the real thing.
const mock = vi.hoisted(() => ({
  catalog: vi.fn(),
  list: vi.fn(),
  health: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
}));
vi.mock("../../api/entities", () => ({ entitiesApi: mock }));

import { AiYamlRenderer } from "./AiYamlRenderer";

const ISSUE_TYPE = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text", required: true },
    { name: "status", role: "status", values: ["open", "in_progress", "done"] },
  ],
  form: [{ name: "title", widget: "text", required: true }],
};

const BOARD = "view: board\nentity: issue\ngroup_by: status\ncard:\n  title: title\n";
const HEALTH = "view: health\ntitle: Project health\n";

function storeWith(text: string, path: string): FileBufferStore {
  return new FileBufferStore({
    readFile: vi.fn(async () => ({ kind: "text" as const, path, size: text.length, text, encoding: "utf-8" as const })),
    writeFile: vi.fn(async () => {}),
  });
}

function renderView(path: string, text: string, openFile?: OpenFile) {
  const store = storeWith(text, path);
  store.ensureLoaded(path);
  const tree = (
    <QueryWrap>
      <WorkspaceSlugProvider value="pm">
        <FileServiceProvider value={investigationFileService("pm", "item1")}>
          <EditModeProvider>
            <FileBufferProvider store={store}>
              <AiYamlRenderer path={path} />
            </FileBufferProvider>
          </EditModeProvider>
        </FileServiceProvider>
      </WorkspaceSlugProvider>
    </QueryWrap>
  );
  return render(openFile ? <OpenFileProvider value={openFile}>{tree}</OpenFileProvider> : tree);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AiYamlRenderer", () => {
  it("renders the live board from the entity API and edits a card through the update route", async () => {
    mock.catalog.mockResolvedValue({ types: [ISSUE_TYPE], diagnostics: [] });
    mock.list.mockResolvedValue({ entities: [{ number: 1, type_name: "issue", fields: { title: "A", status: "open" }, body: "", diagnostics: [] }], invalid: [] });
    mock.update.mockResolvedValue({});

    renderView("/views/board.ai.yaml", BOARD);

    // the projected card shows once the queries resolve
    expect(await screen.findByText("A")).toBeInTheDocument();
    // and the catalog/list were fetched for this (slug, item, type)
    expect(mock.catalog).toHaveBeenCalledWith("pm", "item1");
    expect(mock.list).toHaveBeenCalledWith("pm", "item1", "issue");

    fireEvent.change(screen.getByLabelText("status"), { target: { value: "done" } });
    await waitFor(() => expect(mock.update).toHaveBeenCalledWith("pm", "item1", "issue", 1, { status: "done" }));
  });

  it("degrades a non-view .ai.yaml to a plain structured tree without querying entities", async () => {
    renderView("/notes.ai.yaml", "just: data\ncount: 3\n");
    // the raw yaml tree shows the key; no entity fetch is attempted
    expect(await screen.findByText(/just/)).toBeInTheDocument();
    expect(mock.list).not.toHaveBeenCalled();
  });

  it("jumps a health finding to its record file via the openFile context", async () => {
    mock.catalog.mockResolvedValue({ types: [ISSUE_TYPE], diagnostics: [] });
    mock.health.mockResolvedValue({
      findings: [{ type_name: "issue", number: 2, level: "error", message: "boom" }],
    });
    const openFile = vi.fn();

    renderView("/views/health.ai.yaml", HEALTH, openFile);

    // the finding is a clickable button once catalog + health resolve
    const btn = await screen.findByRole("button", { name: /issue #2/ });
    fireEvent.click(btn);
    // records_path "issues" + number 2 → the record file the operator must fix
    expect(openFile).toHaveBeenCalledWith("/issues/2.md");
  });

  it("renders health findings as plain rows when no openFile context is present", async () => {
    mock.catalog.mockResolvedValue({ types: [ISSUE_TYPE], diagnostics: [] });
    mock.health.mockResolvedValue({
      findings: [{ type_name: "issue", number: 2, level: "error", message: "boom" }],
    });

    renderView("/views/health.ai.yaml", HEALTH);

    expect(await screen.findByText("boom")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /issue #2/ })).not.toBeInTheDocument();
  });
});
