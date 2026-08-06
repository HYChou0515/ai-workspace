// @vitest-environment happy-dom
/**
 * #698 — the second-party plug-in seam, exercised through the WHOLE path: a
 * `.ai.yaml` file's bytes → `parseViewSpec` → the dispatcher → the registered
 * renderer. That entry point is the point of this file.
 *
 * `viewKindRegistry.test.tsx` calls `resolveViewRenderer` directly, which
 * bypasses the parser — so it stayed green while the real path (open a
 * `view: chart` file in the workspace) degraded to a raw YAML tree and the
 * unsupported-kind notice was unreachable. Never assert this seam from there.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../../api/fileService";
import { EditModeProvider } from "../../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../../hooks/fileBuffer";
import { useFileBuffer } from "../../hooks/fileBuffer";
import { WorkspaceSlugProvider } from "../../hooks/useWorkspaceSlug";
import { QueryWrap } from "../../test/queryWrapper";
import { registerViewKind, unregisterViewKind } from "./viewKindRegistry";

const mock = vi.hoisted(() => ({
  catalog: vi.fn(),
  list: vi.fn(),
  health: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
}));
vi.mock("../../api/entities", () => ({ entitiesApi: mock }));

import { AiYamlRenderer } from "./AiYamlRenderer";

/** A buffer store serving a whole workspace, so a plug-in kind can read a file
 * OTHER than the view file it was configured by (the #698 core capability). */
function storeWithFiles(files: Record<string, string>): FileBufferStore {
  return new FileBufferStore({
    readFile: vi.fn(async (path: string) => {
      const text = files[path];
      if (text === undefined) throw new Error(`no such file: ${path}`);
      return { kind: "text" as const, path, size: text.length, text, encoding: "utf-8" as const };
    }),
    writeFile: vi.fn(async () => {}),
  });
}

function renderView(path: string, files: Record<string, string>) {
  const store = storeWithFiles(files);
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

const registered: string[] = [];
function register(def: Parameters<typeof registerViewKind>[0]) {
  registerViewKind(def);
  registered.push(def.kind);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  while (registered.length) unregisterViewKind(registered.pop()!);
});

describe("#698 second-party view kinds", () => {
  it("shows the unsupported-kind notice for a kind nobody registered, instead of degrading to raw YAML", async () => {
    renderView("/views/mystery.ai.yaml", { "/views/mystery.ai.yaml": "view: nosuchkind\nentity: issue\n" });

    expect(await screen.findByText(/unsupported view kind: nosuchkind/i)).toBeInTheDocument();
    // The old behaviour — falling through to the structured YAML tree — is what
    // made the registry's documented fallback dead code. That tree is gone.
    expect(document.querySelector(".json-tree")).toBeNull();
  });

  it("renders a registered second-party kind from the view file's bytes", async () => {
    register({
      kind: "acme-chart",
      Component: ({ spec }) => <div data-testid="acme">charting {spec.entity}</div>,
      needsEntity: true,
      ownsEmptyState: true,
      suppressQuickCreate: true,
    });
    mock.catalog.mockResolvedValue({ types: [], diagnostics: [] });
    mock.list.mockResolvedValue({ entities: [], invalid: [] });

    renderView("/views/chart.ai.yaml", { "/views/chart.ai.yaml": "view: acme-chart\nentity: issue\n" });

    expect(await screen.findByTestId("acme")).toHaveTextContent("charting issue");
  });

  // THE core capability: a kind that is not entity-bound at all. It declares no
  // `entity:`, and gets its data by reading ANY workspace file named by its own
  // custom key in the same .ai.yaml.
  it("renders a kind that declares no entity and reads another workspace file named by a custom key", async () => {
    function WaferMap({ spec }: { spec: { source?: string } }) {
      const { entry } = useFileBuffer(String(spec.source));
      return <div data-testid="wafer">{entry.status === "ready" ? entry.text : "loading"}</div>;
    }
    // Deliberately declares NOTHING beyond its component: a file-reading kind
    // shouldn't have to know about entity empty-states or quick-create.
    register({ kind: "acme-wafer", Component: ({ spec }) => <WaferMap spec={spec as { source?: string }} /> });

    renderView("/views/wafer.ai.yaml", {
      "/views/wafer.ai.yaml": "view: acme-wafer\nsource: /data/wafer.csv\n",
      "/data/wafer.csv": "lot,yield\nA1,0.97\n",
    });

    expect(await screen.findByTestId("wafer")).toHaveTextContent("A1,0.97");
    // no entity in the spec ⇒ the entity endpoints are never touched
    expect(mock.list).not.toHaveBeenCalled();
    // ...and no entity-shaped chrome gatecrashes a view that draws a file.
    // Both of these used to fire for every file-reading kind: the item has no
    // entity records (so "empty"), and no schema for "" (so "No schema for —").
    expect(screen.queryByText(/records yet/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/no schema for/i)).not.toBeInTheDocument();
  });

  it("still degrades a .ai.yaml with no `view:` key to the structured tree", async () => {
    renderView("/notes.ai.yaml", { "/notes.ai.yaml": "just: data\ncount: 3\n" });

    expect(await screen.findByText(/just/)).toBeInTheDocument();
    expect(mock.list).not.toHaveBeenCalled();
  });

  it("tells the user when an entity-bound kind is missing its `entity:`, rather than silently showing raw YAML", async () => {
    register({
      kind: "acme-needs-entity",
      Component: () => <div data-testid="never">should not render</div>,
      needsEntity: true,
    });

    renderView("/views/broken.ai.yaml", { "/views/broken.ai.yaml": "view: acme-needs-entity\n" });

    // the message spans <code> elements, so assert on the banner's whole text
    expect(await screen.findByRole("status")).toHaveTextContent(/needs an entity: naming which records/i);
    expect(screen.queryByTestId("never")).not.toBeInTheDocument();
  });

  // The whole point of this seam is code the platform team did not write. There
  // was no error boundary anywhere between `createRoot` and the view, so one
  // throw in a plug-in unmounted the ENTIRE app — a blank page, not a blank
  // panel.
  it("contains a throwing plug-in inside its own panel instead of taking the app down", async () => {
    register({
      kind: "acme-explodes",
      Component: () => {
        throw new Error("plug-in blew up");
      },
    });

    renderView("/views/boom.ai.yaml", { "/views/boom.ai.yaml": "view: acme-explodes\n" });

    expect(await screen.findByText(/this view failed to render/i)).toBeInTheDocument();
    // the surrounding shell survived — the panel is still on the page
    expect(document.querySelector(".ev-panel")).not.toBeNull();
  });

  it("refuses a duplicate registration instead of silently replacing the incumbent", () => {
    register({ kind: "acme-dup", Component: () => <div /> });
    expect(() => registerViewKind({ kind: "acme-dup", Component: () => <div /> })).toThrow(/acme-dup/);
  });

  // `health` is rendered by the container BEFORE the dispatcher, so it isn't a
  // registry entry — which meant registering it used to succeed and produce a
  // component that simply never rendered. That is the silent, import-order
  // outcome the duplicate check exists to prevent, on the one built-in name a
  // plug-in author might plausibly reuse.
  it("refuses a name the container answers to, rather than accepting a kind that can never render", () => {
    expect(() => registerViewKind({ kind: "health", Component: () => <div data-testid="never" /> })).toThrow(
      /reserved/i,
    );
  });
});
