// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen, waitFor } from "@testing-library/react";

import { QueryWrap } from "../../test/queryWrapper";

// KB views read through TanStack Query — wrap every render with a client.
const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(ui, { wrapper: QueryWrap });
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import type { KbApi, KbDocChunk, KbRenderedDoc } from "../../api/kb";
import { BreadcrumbProvider, useBreadcrumbTrail } from "../../hooks/breadcrumbs";
import { KbDocPage } from "./KbDocPage";

function TrailProbe() {
  const trail = useBreadcrumbTrail();
  return (
    <ul data-testid="trail">
      {trail.map((c, i) => (
        <li key={i} data-to={c.to ?? ""}>
          {c.label}
        </li>
      ))}
    </ul>
  );
}

function mkDoc(
  over: Partial<KbRenderedDoc> & Pick<KbRenderedDoc, "filename" | "markdown">,
): KbRenderedDoc {
  return {
    document_id: "col-1/u/doc.md",
    collection_id: "col-1",
    file_id: "blob-1",
    content_type: "text/markdown",
    size: 1024,
    chunks: 2,
    cited: 0,
    created_by: "u",
    updated_at: Date.UTC(2026, 4, 20),
    status: "ready",
    ...over,
  };
}

function fakeClient(doc: KbRenderedDoc, chunks: KbDocChunk[] = []): KbApi {
  return {
    renderDocument: async () => doc,
    getDocChunks: async () => chunks,
  } as unknown as KbApi;
}

describe("KbDocPage", () => {
  afterEach(cleanup);

  it("renders the document named by the splat route param + highlights ?hl", async () => {
    const client = fakeClient(
      mkDoc({ filename: "reflow.md", markdown: "# Reflow\n\nZone three drifted under load." }),
    );
    const { container } = render(
      <MemoryRouter initialEntries={["/kb/doc/col-1/u/reflow.md?hl=Zone%20three%20drifted%20under%20load"]}>
        <Routes>
          <Route path="/kb/doc/*" element={<KbDocPage client={client} />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByText(/Zone three drifted under load/)).toBeInTheDocument(),
    );
    await waitFor(() => {
      const mark = container.querySelector("mark.kb-hl");
      expect(mark?.textContent).toBe("Zone three drifted under load");
    });
  });

  it("toggles from the file view to a chunks view with per-chunk cited counts", async () => {
    const client = fakeClient(
      mkDoc({ filename: "reflow.md", markdown: "# Reflow\n\nbody text here" }),
      [
        { chunk_id: "col-1/u/reflow.md#0", seq: 0, start: 0, end: 8, text: "# Reflow", cited: 3 },
        { chunk_id: "col-1/u/reflow.md#1", seq: 1, start: 9, end: 23, text: "body text here", cited: 0 },
      ],
    );
    render(
      <MemoryRouter initialEntries={["/kb/doc/col-1/u/reflow.md"]}>
        <Routes>
          <Route path="/kb/doc/*" element={<KbDocPage client={client} />} />
        </Routes>
      </MemoryRouter>,
    );
    // file view is shown first
    await screen.findByText(/body text here/);

    // toggle reports the chunk count and switches to the chunks list
    await userEvent.click(screen.getByRole("tab", { name: /chunks \(2\)/i }));
    const chunk0 = (await screen.findByText("# Reflow")).closest(".kb-chunk")!;
    expect(chunk0).toHaveTextContent("3 cited");
    expect(chunk0).toHaveTextContent("#0");
  });

  it("publishes a Home › Knowledge base › {doc} breadcrumb once the doc loads", async () => {
    const client = fakeClient(mkDoc({ filename: "reflow.md", markdown: "# Reflow\n\nbody" }));
    render(
      <MemoryRouter initialEntries={["/kb/doc/col-1/u/reflow.md"]}>
        <BreadcrumbProvider>
          <Routes>
            <Route path="/kb/doc/*" element={<KbDocPage client={client} />} />
          </Routes>
          <TrailProbe />
        </BreadcrumbProvider>
      </MemoryRouter>,
    );
    await waitFor(() => {
      const items = screen.getByTestId("trail").querySelectorAll("li");
      expect([...items].map((li) => li.textContent)).toEqual([
        "Home",
        "Knowledge base",
        "reflow.md",
      ]);
    });
    const items = screen.getByTestId("trail").querySelectorAll("li");
    expect(items[1].getAttribute("data-to")).toBe("/kb");
  });
});
