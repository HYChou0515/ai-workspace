// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppWorkspace } from "./AppWorkspace";

afterEach(cleanup);

// The 82KB shell is heavy + provider-laden — stub it to assert the wrapper's
// contract (it loads the item via resource_route + feeds it to the shell).
vi.mock("./investigation/WorkspaceShell", () => ({
  WorkspaceShell: ({ item }: { item: { title: string; resource_id: string } }) => (
    <div data-testid="shell">
      {item.title} · {item.resource_id}
    </div>
  ),
}));
vi.mock("../hooks/useResources", () => ({
  useAppManifest: () => ({ resource_route: "/rca-investigation" }),
  useAppItem: () => ({
    resource_id: "rca-investigation/1",
    title: "Oven drift",
    owner: "u",
    severity: "P1",
    status: "triaging",
    product: "MX-7",
  }),
}));
vi.mock("../hooks/useInvestigation", () => ({
  useFiles: () => ({ kind: "ready", items: [], dirs: [], refresh: () => {} }),
}));

describe("AppWorkspace", () => {
  it("loads the item (decoding the slash-bearing id) and feeds the workspace shell", () => {
    render(
      <MemoryRouter initialEntries={["/a/rca/rca-investigation%2F1"]}>
        <Routes>
          <Route path="/a/:slug/:itemId" element={<AppWorkspace />} />
        </Routes>
      </MemoryRouter>,
    );
    const shell = screen.getByTestId("shell");
    expect(shell).toHaveTextContent("Oven drift");
    expect(shell).toHaveTextContent("rca-investigation/1"); // id decoded
  });
});
