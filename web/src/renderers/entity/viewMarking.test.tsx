// @vitest-environment happy-dom
/**
 * #847 PR 3 P3: the view header's `🔗 <name> ▾` — attach a view to an existing
 * marking, a new one, or detach it. The choice is this person's VIEW STATE: the
 * file is not rewritten (they edit the YAML to make it permanent). Exercised
 * through the whole path — a `.ai.yaml`'s bytes → the renderer → the kind.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterAll, afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../../api/fileService";
import { EditModeProvider } from "../../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../../hooks/fileBuffer";
import { MarkingProvider } from "../../hooks/useMarking";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { MarkingStore } from "../../lib/markings";
import { QueryWrap } from "../../test/queryWrapper";
import type { EntityViewProps } from "./types";
import { registerViewKind, unregisterViewKind } from "./viewKindRegistry";

vi.mock("../../api/entities", () => ({
  entitiesApi: { catalog: vi.fn(), list: vi.fn(), health: vi.fn(), create: vi.fn(), update: vi.fn() },
}));

import { AiYamlRenderer } from "./AiYamlRenderer";

const writeFile = vi.fn(async () => {});

function renderView(text: string, store = new MarkingStore()) {
  const buffers = new FileBufferStore({
    readFile: vi.fn(async (path: string) => ({
      kind: "text" as const,
      path,
      size: text.length,
      text,
      encoding: "utf-8" as const,
    })),
    writeFile,
  });
  const path = "/views/grid.ai.yaml";
  buffers.ensureLoaded(path);
  return render(
    <QueryWrap>
      <WorkspaceSlugProvider value="rca">
        <FileServiceProvider value={investigationFileService("rca", "item1")}>
          <EditModeProvider>
            <FileBufferProvider store={buffers}>
              <MarkingProvider store={store}>
                <AiYamlRenderer path={path} />
              </MarkingProvider>
            </FileBufferProvider>
          </EditModeProvider>
        </FileServiceProvider>
      </WorkspaceSlugProvider>
    </QueryWrap>,
  );
}

function Probe({ marking, path }: EntityViewProps) {
  return (
    <div data-testid="probe">
      {marking === undefined ? "unmanaged" : marking === null ? "detached" : `on:${marking}`} {path}
    </div>
  );
}

registerViewKind({ kind: "probe-chart", Component: Probe, ownsEmptyState: true, suppressQuickCreate: true });
registerViewKind({
  kind: "probe-linkable",
  Component: Probe,
  ownsEmptyState: true,
  suppressQuickCreate: true,
  linkable: true,
});

afterEach(() => {
  cleanup();
  writeFile.mockClear();
  localStorage.clear();
});

const ON_FAIL = "view: probe-chart\nmarking: fail\nkeys: [lot]\n";

describe("the view header's marking control", () => {
  it("shows the file's marking and hands it, with the view's path, to the kind", async () => {
    renderView(ON_FAIL);
    expect(await screen.findByTestId("probe")).toHaveTextContent("on:fail /views/grid.ai.yaml");
    expect(screen.getByRole("combobox", { name: /marking/i })).toHaveValue("fail");
  });

  it("detaches without rewriting the file", async () => {
    renderView(ON_FAIL);
    fireEvent.change(await screen.findByRole("combobox", { name: /marking/i }), { target: { value: "" } });
    expect(screen.getByTestId("probe")).toHaveTextContent("detached");
    expect(writeFile).not.toHaveBeenCalled();
  });

  it("attaches to another marking the item already has", async () => {
    const store = new MarkingStore();
    store.set("other", { lot: new Set(["L1"]) }, null);
    renderView(ON_FAIL, store);
    const select = await screen.findByRole("combobox", { name: /marking/i });
    expect([...(select as HTMLSelectElement).options].map((o) => o.value)).toContain("other");
    fireEvent.change(select, { target: { value: "other" } });
    expect(screen.getByTestId("probe")).toHaveTextContent("on:other");
  });

  it("attaches to a new marking by name", async () => {
    renderView(ON_FAIL);
    fireEvent.change(await screen.findByRole("combobox", { name: /marking/i }), { target: { value: "\u0000new" } });
    fireEvent.change(screen.getByRole("textbox", { name: /new marking/i }), { target: { value: "lots" } });
    fireEvent.submit(screen.getByRole("textbox", { name: /new marking/i }));
    expect(screen.getByTestId("probe")).toHaveTextContent("on:lots");
  });

  it("keeps the choice for this view across a remount, as view state", async () => {
    const first = renderView(ON_FAIL);
    fireEvent.change(await screen.findByRole("combobox", { name: /marking/i }), { target: { value: "" } });
    first.unmount();
    renderView(ON_FAIL);
    expect(await screen.findByTestId("probe")).toHaveTextContent("detached");
  });

  it("is absent — and the kind unmanaged — for a view that names neither marking nor keys", async () => {
    renderView("view: probe-chart\n");
    expect(await screen.findByTestId("probe")).toHaveTextContent("unmanaged");
    expect(screen.queryByRole("combobox", { name: /marking/i })).not.toBeInTheDocument();
  });
});

describe("a kind that declares itself linkable", () => {
  it("gets the control on a view whose file names neither marking nor keys", async () => {
    renderView("view: probe-linkable\n");
    expect(await screen.findByTestId("probe")).toHaveTextContent("detached");
    fireEvent.change(screen.getByRole("combobox", { name: /marking/i }), { target: { value: "\u0000new" } });
    fireEvent.change(screen.getByRole("textbox", { name: /new marking/i }), { target: { value: "lots" } });
    fireEvent.submit(screen.getByRole("textbox", { name: /new marking/i }));
    expect(screen.getByTestId("probe")).toHaveTextContent("on:lots");
  });
});

afterAll(() => {
  unregisterViewKind("probe-chart");
  unregisterViewKind("probe-linkable");
});
