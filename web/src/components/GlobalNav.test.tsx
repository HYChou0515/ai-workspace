// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

import { GlobalNav } from "./GlobalNav";
import { BreadcrumbProvider, useBreadcrumbs } from "../hooks/breadcrumbs";
import type { Crumb } from "../hooks/breadcrumbs";
import type { HealthApi } from "../api/health";
import { QueryWrap } from "../test/queryWrapper";

const okHealth: HealthApi = {
  getChecks: async () => ({
    running: false,
    checks: [
      {
        check_id: "c",
        description: "d",
        fast: false,
        status: "pass",
        detail: "",
        latency_ms: 1,
        checked_at: 1,
      },
    ],
  }),
  runChecks: async () => ({ started: true }),
};

vi.mock("../hooks/useResources", () => ({
  useApps: () => [
    { slug: "rca", title: "Root Cause Analysis", description: "x", icon: "flame", color: "#F0502E" },
    { slug: "yield", title: "Yield Tracking", description: "y", icon: "bug", color: "#2D6CC9" },
  ],
}));

function Pub({ crumbs }: { crumbs: Crumb[] }) {
  useBreadcrumbs(crumbs);
  return null;
}

function renderNav(path: string, crumbs: Crumb[] = [], healthClient?: HealthApi) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryWrap>
        <BreadcrumbProvider>
          <Pub crumbs={crumbs} />
          <GlobalNav healthClient={healthClient} />
        </BreadcrumbProvider>
      </QueryWrap>
    </MemoryRouter>,
  );
}

describe("GlobalNav", () => {
  it("brand links home (/)", () => {
    renderNav("/a/rca");
    expect(screen.getByRole("link", { name: /Workspace/ })).toHaveAttribute("href", "/");
  });

  it("renders the published trail: linked crumbs for `to`, plain text for the current page", () => {
    renderNav("/a/rca/123", [
      { label: "Home", to: "/" },
      { label: "RCA", to: "/a/rca" },
      { label: "Bearing noise #1432" },
    ]);
    const nav = screen.getByRole("navigation", { name: /breadcrumb/i });
    expect(within(nav).getByRole("link", { name: "RCA" })).toHaveAttribute("href", "/a/rca");
    // The current page is not a link — it's the leaf, shown as text.
    expect(within(nav).queryByRole("link", { name: "Bearing noise #1432" })).toBeNull();
    expect(within(nav).getByText("Bearing noise #1432")).toBeInTheDocument();
  });

  it("switcher dropdown jumps straight to any App, the Knowledge base, or Diagnostics", () => {
    renderNav("/a/rca");
    fireEvent.click(screen.getByRole("button", { name: /switch/i }));
    const menu = screen.getByRole("dialog");
    expect(within(menu).getByRole("link", { name: /Root Cause Analysis/ })).toHaveAttribute(
      "href",
      "/a/rca",
    );
    expect(within(menu).getByRole("link", { name: /Yield Tracking/ })).toHaveAttribute(
      "href",
      "/a/yield",
    );
    expect(within(menu).getByRole("link", { name: /Knowledge base/i })).toHaveAttribute(
      "href",
      "/kb",
    );
    expect(within(menu).getByRole("link", { name: /Diagnostics/i })).toHaveAttribute(
      "href",
      "/diagnostics",
    );
  });

  it("switcher marks the current location (the App you're inside)", () => {
    renderNav("/a/yield/42");
    fireEvent.click(screen.getByRole("button", { name: /switch/i }));
    const menu = screen.getByRole("dialog");
    expect(within(menu).getByRole("link", { name: /Yield Tracking/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(menu).getByRole("link", { name: /Root Cause Analysis/ })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("shows the AI-health dot linking to /diagnostics", async () => {
    renderNav("/a/rca", [], okHealth);
    const dot = await screen.findByRole("link", { name: /AI features are working/i });
    expect(dot).toHaveAttribute("href", "/diagnostics");
  });
});
