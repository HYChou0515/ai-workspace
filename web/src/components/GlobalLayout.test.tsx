// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { useEffect } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

import { GlobalLayout } from "./GlobalLayout";
import { HttpError } from "../api/http";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useNavChrome } from "../hooks/useNavChrome";
import { reportWriteFailure, resetWriteFailures } from "../lib/writeFailures";
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

function mount(child: React.ReactElement) {
  return render(
    <MemoryRouter initialEntries={["/a/rca"]}>
      <QueryWrap>
        <Routes>
          <Route element={<GlobalLayout />}>
            <Route path="/a/:slug" element={child} />
          </Route>
        </Routes>
      </QueryWrap>
    </MemoryRouter>,
  );
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

  // Every route in the app nests under this layout, so mounting the notice here
  // is what makes "a write never fails silently" true everywhere rather than on
  // the one page someone remembered.
  it("carries the write-failure notice for every page below it", async () => {
    resetWriteFailures();
    mount(<Child />);
    expect(screen.queryByTestId("write-failure")).toBeNull();

    await act(async () => reportWriteFailure(new HttpError(403, "403 Forbidden")));
    expect(screen.getByTestId("write-failure")).toBeInTheDocument();
  });

  // A chat-first workspace hides the top bar. The notice is not chrome — it is
  // the only thing standing between a lost edit and silence, so it stays.
  it("still shows it on a page that hides the global bar", async () => {
    resetWriteFailures();
    function BareChild() {
      const { setHidden } = useNavChrome();
      useEffect(() => setHidden(true), [setHidden]);
      return <div data-testid="child">child page</div>;
    }
    mount(<BareChild />);
    expect(screen.queryByRole("link", { name: /Workspace/ })).toBeNull();

    await act(async () => reportWriteFailure(new HttpError(500, "boom")));
    expect(screen.getByTestId("write-failure")).toBeInTheDocument();
  });
});
