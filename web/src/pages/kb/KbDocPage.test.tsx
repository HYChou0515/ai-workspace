// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import type { KbApi, KbRenderedDoc } from "../../api/kb";
import { KbDocPage } from "./KbDocPage";

function fakeClient(doc: KbRenderedDoc): KbApi {
  return { renderDocument: async () => doc } as unknown as KbApi;
}

describe("KbDocPage", () => {
  afterEach(cleanup);

  it("renders the document named by the splat route param + highlights ?hl", async () => {
    const client = fakeClient({
      filename: "reflow.md",
      collection_id: "col-1",
      markdown: "# Reflow\n\nZone three drifted under load.",
    });
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
});
