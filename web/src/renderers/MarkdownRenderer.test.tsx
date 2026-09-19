// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../api/fileService";
import { EditModeProvider, useEditMode } from "../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../hooks/fileBuffer";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import { QueryWrap } from "../test/queryWrapper";
import { MarkdownBody, MarkdownRenderer } from "./MarkdownRenderer";

afterEach(cleanup);

/** Flip shared edit-mode on for a path via the real API (as the Edit tab does). */
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

async function renderMd(text: string, path = "/notes/x.md", opts: { editing?: boolean } = {}) {
  const store = storeWith(text, path);
  store.ensureLoaded(path);
  await new Promise((r) => setTimeout(r, 0));
  const result = render(
    <QueryWrap>
      <WorkspaceSlugProvider value="pm">
        <FileServiceProvider value={investigationFileService("pm", "item1")}>
          <EditModeProvider>
            <FileBufferProvider store={store}>
              {opts.editing ? <EnableEdit path={path} /> : null}
              <MarkdownRenderer path={path} />
            </FileBufferProvider>
          </EditModeProvider>
        </FileServiceProvider>
      </WorkspaceSlugProvider>
    </QueryWrap>,
  );
  await new Promise((r) => setTimeout(r, 0)); // flush edit-mode toggle + shadow inject
  return result;
}

const MARP = "---\nmarp: true\ntheme: default\n---\n\n# Slide One\n\n---\n\n# Slide Two";

describe("MarkdownRenderer — Marp routing", () => {
  it("renders a marp:true document as a slide deck", async () => {
    const { container } = await renderMd(MARP);
    const host = container.querySelector('[data-testid="marp-host"]') as HTMLElement | null;
    expect(host).toBeInTheDocument();
    expect(host?.shadowRoot?.querySelectorAll("section")).toHaveLength(2);
  });

  it("renders a plain markdown file as prose, not a deck", async () => {
    const { container } = await renderMd("# Just Notes\n\nsome text");
    expect(container.querySelector('[data-testid="marp-host"]')).toBeNull();
    expect(screen.getByText("Just Notes")).toBeInTheDocument();
  });

  it("shows the raw editor (not the deck) while a marp file is being edited", async () => {
    const { container } = await renderMd(MARP, "/notes/x.md", { editing: true });
    expect(container.querySelector('[data-testid="marp-host"]')).toBeNull();
  });
});

// The service knows how to resolve a ref against a document, but only if the
// renderer tells it WHICH document. Asserting the resolved URL at the service
// alone left that wiring untested — and dropping it is exactly what made an
// image render only when its markdown happened to sit at the workspace root.
describe("MarkdownRenderer — an image next to the document", () => {
  it("resolves a sibling image against the document's own folder", async () => {
    const { container } = await renderMd("# Notes\n\n![chart](./plot.png)\n", "/reports/r.md");
    const img = container.querySelector("img");
    expect(img).toHaveAttribute("src", "/api/a/pm/items/item1/files/reports/plot.png");
  });

  it("resolves a sibling link against the document's own folder", async () => {
    const { container } = await renderMd("[the data](./rows.csv)\n", "/reports/r.md");
    const link = container.querySelector("a");
    expect(link).toHaveAttribute("href", "/api/a/pm/items/item1/files/reports/rows.csv");
  });
});

// MarkdownBody outside a workspace: the onboarding modal holds markdown in
// memory on the Launcher / AppDashboard, where no FileServiceProvider exists.
// A caller that says how a ref resolves gets the same pipeline without one;
// a caller that says nothing still needs the provider — that contract stays.
describe("MarkdownBody — resolving refs without a file service", () => {
  it("renders with a resolveUrl and no provider, routing images and links through it", () => {
    const resolveUrl = (src: string) => `/resolved/${src}`;
    const { container } = render(
      <MarkdownBody text="![shot](assets/a.png)\n\n[docs](guide.md)" resolveUrl={resolveUrl} />,
    );
    expect(container.querySelector("img")).toHaveAttribute("src", "/resolved/assets/a.png");
    expect(container.querySelector("a")).toHaveAttribute("href", "/resolved/guide.md");
  });

  it("still requires the provider when no resolveUrl is given", () => {
    vi.spyOn(console, "error").mockImplementation(() => {}); // React logs the throw
    expect(() => render(<MarkdownBody text="hi" path="/x.md" />)).toThrow(/FileServiceProvider/);
    vi.restoreAllMocks();
  });

  it("takes the compact variant on request", () => {
    const { container } = render(<MarkdownBody text="hi" resolveUrl={(s) => s} compact />);
    expect(container.querySelector("article")).toHaveClass("md-body", "md-compact");
    const plain = render(<MarkdownBody text="hi" resolveUrl={(s) => s} />);
    expect(plain.container.querySelector("article")).not.toHaveClass("md-compact");
  });
});
