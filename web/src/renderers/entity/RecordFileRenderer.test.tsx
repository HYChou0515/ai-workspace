// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../../api/fileService";
import { EditModeProvider } from "../../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../../hooks/fileBuffer";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { makeTestQueryClient, QueryWrap } from "../../test/queryWrapper";

// Stub only the network leaf; keep the real EntityConflictError so the hook's
// `instanceof` conflict branch fires exactly as in production.
const mock = vi.hoisted(() => ({
  catalog: vi.fn(),
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
}));
vi.mock("../../api/entities", async () => {
  const actual = await vi.importActual<typeof import("../../api/entities")>("../../api/entities");
  return { ...actual, entitiesApi: mock };
});

// The record editor's body + YAML ride the lazy Monaco stack — stub it with a
// textarea keyed on `ariaLabel` (matching EntityFileEditor.test).
vi.mock("../../components/MonacoEditor", () => ({
  MonacoEditor: ({
    value,
    onChange,
    readOnly,
    ariaLabel,
  }: {
    value: string;
    onChange?: (next: string) => void;
    readOnly?: boolean;
    ariaLabel?: string;
  }) => (
    <textarea
      aria-label={ariaLabel}
      value={value}
      disabled={readOnly}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

// §E — the renderer derives the member's write permission through this hook;
// stub the seam (its own decision tree is covered in useItemCanWrite.test).
vi.mock("../../hooks/useItemCanWrite", () => ({ useItemCanWrite: vi.fn(() => true) }));

import { EntityConflictError } from "../../api/entities";
import { useItemCanWrite } from "../../hooks/useItemCanWrite";
import { RecordFileRenderer } from "./RecordFileRenderer";

const ISSUE_TYPE = {
  name: "issue",
  records_path: "issues",
  fields: [
    { name: "title", role: "text", required: true },
    { name: "status", role: "status", values: ["open", "done"] },
  ],
  form: [{ name: "title", widget: "text", required: true }],
};

const RECORD5 = {
  number: 5,
  type_name: "issue",
  fields: { title: "A", status: "open" },
  body: "orig",
  diagnostics: [],
  version: "v1",
};

function storeWith(text: string, path: string): FileBufferStore {
  return new FileBufferStore({
    readFile: vi.fn(async () => ({ kind: "text" as const, path, size: text.length, text, encoding: "utf-8" as const })),
    writeFile: vi.fn(async () => {}),
  });
}

function renderAt(path: string, text = "") {
  const store = storeWith(text, path);
  store.ensureLoaded(path);
  return render(
    <QueryWrap>
      <WorkspaceSlugProvider value="pm">
        <FileServiceProvider value={investigationFileService("pm", "item1")}>
          <EditModeProvider>
            <FileBufferProvider store={store}>
              <RecordFileRenderer path={path} />
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

describe("RecordFileRenderer (§C2)", () => {
  // clearAllMocks wipes call history but ALSO the per-test return overrides —
  // re-arm the default (writable member) before each test.
  beforeEach(() => vi.mocked(useItemCanWrite).mockReturnValue(true));

  it("shows a read-only member the record WITHOUT live write affordances (§E)", async () => {
    // The renderer used to omit `canWrite`, and useEntityWrite's `?? true`
    // default silently made every read-only member's editor look writable —
    // fields enabled, Save active, and the save then 403'd server-side.
    vi.mocked(useItemCanWrite).mockReturnValue(false);
    mock.catalog.mockResolvedValue({ types: [ISSUE_TYPE], diagnostics: [] });
    mock.list.mockResolvedValue({ entities: [RECORD5], invalid: [] });

    renderAt("/issues/5.md");

    expect(await screen.findByLabelText("title")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("renders the entity file editor for a record file and saves through the update route", async () => {
    mock.catalog.mockResolvedValue({ types: [ISSUE_TYPE], diagnostics: [] });
    mock.list.mockResolvedValue({ entities: [RECORD5], invalid: [] });
    mock.update.mockResolvedValue({});

    renderAt("/issues/5.md");

    expect(await screen.findByLabelText("title")).toHaveValue("A");
    expect(mock.list).toHaveBeenCalledWith("pm", "item1", "issue");

    fireEvent.change(screen.getByLabelText("status"), { target: { value: "done" } });
    fireEvent.change(screen.getByLabelText("body"), { target: { value: "new" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // The frontmatter patch + body ride the shared update route with the record's
    // version echoed as expected_version (§B1/§B2/§C6).
    await waitFor(() =>
      expect(mock.update).toHaveBeenCalledWith(
        "pm",
        "item1",
        "issue",
        5,
        expect.objectContaining({ status: "done" }),
        "v1",
        "new",
      ),
    );
  });

  it("degrades a numeric .md that is not a record to plain markdown (no entity list fetch)", async () => {
    mock.catalog.mockResolvedValue({ types: [ISSUE_TYPE], diagnostics: [] });

    renderAt("/notes/7.md", "# Hello world");

    // `notes` is not a records_path → it's just a doc that happens to be named 7.md.
    expect(await screen.findByText("Hello world")).toBeInTheDocument();
    expect(mock.list).not.toHaveBeenCalled();
  });

  it("degrades to plain markdown when the numbered record does not exist (§D)", async () => {
    mock.catalog.mockResolvedValue({ types: [ISSUE_TYPE], diagnostics: [] });
    mock.list.mockResolvedValue({ entities: [RECORD5], invalid: [] });

    renderAt("/issues/999.md", "# Orphan file");

    // #999 isn't in the projection (unparseable / stray file) → don't blank out;
    // fall back to the raw markdown so the user can still see + fix it.
    expect(await screen.findByText("Orphan file")).toBeInTheDocument();
  });

  it("re-seeds the editor when switching to another record file (no cross-record state bleed)", async () => {
    // #1 state-bleed bug: after editing issues/5.md, opening issues/6.md showed
    // #5's title/date. The editor seeds its form from `record` via useState (run
    // once on mount); the whole IDE has ONE FileView mount point, so a tab switch
    // only swaps the `path` prop and the reused EntityFileEditor keeps #5's
    // values. A per-record key must remount it. A stable QueryClient keeps the
    // catalog/list cached so the switch renders straight from cache — no loading
    // flash that would remount the editor and mask the bug.
    mock.catalog.mockResolvedValue({ types: [ISSUE_TYPE], diagnostics: [] });
    mock.list.mockResolvedValue({
      entities: [RECORD5, { ...RECORD5, number: 6, fields: { title: "B", status: "done" }, body: "six" }],
      invalid: [],
    });

    const client = makeTestQueryClient();
    const store = storeWith("", "/issues/5.md");
    const tree = (path: string) => (
      <QueryWrap client={client}>
        <WorkspaceSlugProvider value="pm">
          <FileServiceProvider value={investigationFileService("pm", "item1")}>
            <EditModeProvider>
              <FileBufferProvider store={store}>
                <RecordFileRenderer path={path} />
              </FileBufferProvider>
            </EditModeProvider>
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>
    );

    const { rerender } = render(tree("/issues/5.md"));
    expect(await screen.findByLabelText("title")).toHaveValue("A");

    rerender(tree("/issues/6.md"));
    expect(await screen.findByLabelText("title")).toHaveValue("B");
    expect(screen.getByLabelText("status")).toHaveValue("done");
  });

  it("surfaces a 409 as a non-blocking conflict banner (§B2)", async () => {
    mock.catalog.mockResolvedValue({ types: [ISSUE_TYPE], diagnostics: [] });
    mock.list.mockResolvedValue({ entities: [RECORD5], invalid: [] });
    mock.update.mockRejectedValueOnce(new EntityConflictError());

    renderAt("/issues/5.md");

    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/someone else changed/i);
  });
});
