// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import type { KbApi, KbRenderedDoc } from "../../api/kb";
import { KbDocViewer } from "./KbDocViewer";

function fakeClient(docs: Record<string, KbRenderedDoc>): KbApi {
  return {
    renderDocument: async (id: string) => {
      const d = docs[id];
      if (!d) throw new Error(`not found: ${id}`);
      return d;
    },
  } as unknown as KbApi;
}

describe("KbDocViewer", () => {
  afterEach(cleanup);

  it("renders the document body and the cited passage callout", async () => {
    const client = fakeClient({
      "col-1/u/reflow.md": {
        filename: "reflow.md",
        collection_id: "col-1",
        markdown: "# Reflow\n\nZone three drifted under load.",
      },
    });
    const { container } = render(
      <KbDocViewer
        documentId="col-1/u/reflow.md"
        snippet="Zone three drifted under load"
        onClose={() => {}}
        client={client}
      />,
    );
    await waitFor(() => expect(screen.getByText("Cited passage")).toBeInTheDocument());
    // the cited passage is highlighted in place within the rendered body
    await waitFor(() => {
      const mark = container.querySelector("mark.kb-hl");
      expect(mark?.textContent).toBe("Zone three drifted under load");
    });
  });

  it("follows a kb:// link to the target document in-place", async () => {
    const client = fakeClient({
      "col-1/u/a.md": {
        filename: "a.md",
        collection_id: "col-1",
        markdown: "See [the other doc](kb://doc/col-1/u/b.md).",
      },
      "col-1/u/b.md": {
        filename: "b.md",
        collection_id: "col-1",
        markdown: "# B\n\nThe linked document.",
      },
    });
    render(<KbDocViewer documentId="col-1/u/a.md" onClose={() => {}} client={client} />);

    const link = await screen.findByRole("button", { name: "the other doc" });
    await userEvent.click(link);

    await waitFor(() => expect(screen.getByText(/The linked document/)).toBeInTheDocument());
  });
});
