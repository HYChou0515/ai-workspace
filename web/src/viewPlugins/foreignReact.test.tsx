// @vitest-environment happy-dom
/**
 * A plugin that bundles its OWN React must cost its panel, not the app
 * (#847/#848 PR1 P4).
 *
 * In the planning spike such a plugin threw `Cannot read properties of null
 * (reading 'useState')` during render and took the whole host tree down. The
 * fixture here is the real thing, not a component that throws a look-alike
 * message: a second, separately loaded copy of React (Node's require cache
 * cleared around it), whose hook dispatcher the host renderer never sets.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { createRequire } from "node:module";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../api/fileService";
import { EditModeProvider } from "../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../hooks/fileBuffer";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import { AiYamlRenderer } from "../renderers/entity/AiYamlRenderer";
import { registerViewKind, unregisterViewKind } from "../renderers/entity/viewKindRegistry";
import { QueryWrap } from "../test/queryWrapper";

vi.mock("../api/entities", () => ({
  entitiesApi: {
    catalog: vi.fn(async () => ({ types: [], diagnostics: [] })),
    list: vi.fn(async () => ({ entities: [], invalid: [] })),
    health: vi.fn(async () => ({ findings: [] })),
    create: vi.fn(),
    update: vi.fn(),
  },
}));

/** A second React instance, as a plugin that bundled its own would carry. */
function foreignReact(): typeof import("react") {
  const req = createRequire(import.meta.url);
  const ids = Object.keys(req.cache).filter((k) => /[/\\]node_modules[/\\]react[/\\]/.test(k));
  const saved = Object.fromEntries(ids.map((k) => [k, req.cache[k]]));
  for (const k of ids) delete req.cache[k];
  try {
    return req("react") as typeof import("react");
  } finally {
    for (const k of Object.keys(req.cache).filter((k) => /[/\\]node_modules[/\\]react[/\\]/.test(k))) delete req.cache[k];
    Object.assign(req.cache, saved);
  }
}

afterEach(() => {
  cleanup();
  unregisterViewKind("bundled-react");
});

describe("a plugin that bundles its own React", () => {
  it("fails inside its own panel; the rest of the app keeps rendering", async () => {
    const theirs = foreignReact();
    const hostReact = await import("react");
    expect(theirs).not.toBe(hostReact); // the fixture really is a second copy
    registerViewKind({
      kind: "bundled-react",
      Component: () => {
        const [n] = theirs.useState(0);
        return <p>count {n}</p>;
      },
    });
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});

    const path = "/views/x.ai.yaml";
    const text = "view: bundled-react\ntitle: Theirs\n";
    const store = new FileBufferStore({
      readFile: vi.fn(async () => ({ kind: "text" as const, path, size: text.length, text, encoding: "utf-8" as const })),
      writeFile: vi.fn(async () => {}),
    });
    store.ensureLoaded(path);
    render(
      <QueryWrap>
        <WorkspaceSlugProvider value="pm">
          <FileServiceProvider value={investigationFileService("pm", "item1")}>
            <EditModeProvider>
              <FileBufferProvider store={store}>
                <p>the host is still here</p>
                <AiYamlRenderer path={path} />
              </FileBufferProvider>
            </EditModeProvider>
          </FileServiceProvider>
        </WorkspaceSlugProvider>
      </QueryWrap>,
    );

    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("This view failed to render");
    expect(banner).toHaveTextContent("bundled-react");
    expect(banner).toHaveTextContent(/useState/);
    expect(screen.getByText("the host is still here")).toBeInTheDocument();
    errors.mockRestore();
  });
});
