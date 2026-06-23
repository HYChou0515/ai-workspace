// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

import { GlobalLayout } from "./GlobalLayout";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { QueryWrap } from "../test/queryWrapper";

vi.mock("../hooks/useResources", () => ({
  useApps: () => [
    { slug: "rca", title: "Root Cause Analysis", description: "x", icon: "flame", color: "#F0502E" },
  ],
}));

// Keep the health dot off the network in this integration test.
vi.mock("../api/health", () => ({
  healthApi: {
    getChecks: async () => ({ running: false, checks: [] }),
    runChecks: async () => ({ started: true }),
  },
}));

function Child() {
  useBreadcrumbs([
    { label: "Home", to: "/" },
    { label: "RCA" },
  ]);
  return <div data-testid="child">child page</div>;
}

describe("GlobalLayout", () => {
  it("wraps child routes with the global bar and shares the breadcrumb trail a page publishes", () => {
    render(
      <MemoryRouter initialEntries={["/a/rca"]}>
        <QueryWrap>
          <Routes>
            <Route element={<GlobalLayout />}>
              <Route path="/a/:slug" element={<Child />} />
            </Route>
          </Routes>
        </QueryWrap>
      </MemoryRouter>,
    );
    // The bar is present...
    expect(screen.getByRole("link", { name: /Workspace/ })).toHaveAttribute("href", "/");
    // ...the child renders through the Outlet...
    expect(screen.getByTestId("child")).toBeInTheDocument();
    // ...and the child's published crumbs reach the bar (shared provider):
    // intermediate "Home" is a link, the leaf "RCA" is shown as text.
    const nav = screen.getByRole("navigation", { name: /breadcrumb/i });
    expect(within(nav).getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
    expect(within(nav).getByText("RCA")).toBeInTheDocument();
  });
});
