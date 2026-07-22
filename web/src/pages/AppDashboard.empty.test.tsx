// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../test/queryWrapper";
import { AppDashboard } from "./AppDashboard";

afterEach(cleanup);
beforeEach(() => localStorage.clear());

vi.mock("../hooks/useCurrentUser", () => ({
  useCurrentUser: () => "alice",
  useCurrentUserState: () => ({ id: "alice", ready: true }),
}));
vi.mock("../hooks/useUsers", () => ({
  useUsers: () => [],
  useUser: (id: string) => ({ id, name: id, section: "", email: "", photo_url: null }),
}));

// A brand-new App with zero items — the first-user case.
vi.mock("../hooks/useResources", () => ({
  useAppManifest: () => ({
    slug: "rca",
    title: "Root Cause Analysis",
    description: "Structured failure investigations.",
    icon: "flame",
    color: "#F0502E",
    item: { noun: "Investigation", noun_plural: "Investigations", create_label: "Start Investigation" },
    layout: { list: ["severity", "status", "product"], breadcrumb: [], statusbar: [] },
    labels: { severity: "Severity", status: "Status", product: "Product" },
    fields: [],
    field_styles: {},
    lifecycle: { status_field: "status", closing_states: ["resolved"] },
    resource_route: "/rca-investigation",
    function: { workspace: true, sandbox: true, terminal: true },
    agent: { picker: [] },
    default_profile: "default",
  }),
  useAppItems: () => ({ items: [], isPending: false }),
}));

function renderDash() {
  return render(
    <QueryWrap>
      <MemoryRouter initialEntries={["/a/rca"]}>
        <Routes>
          <Route path="/a/:slug" element={<AppDashboard />} />
        </Routes>
      </MemoryRouter>
    </QueryWrap>,
  );
}

describe("AppDashboard empty App (first-user)", () => {
  it("shows a 'create your first' hero, not a zeroed '0 open · 0 critical' counter", () => {
    renderDash();
    expect(screen.queryByText(/0 open/i)).toBeNull();
    expect(screen.getByText(/no investigations yet/i)).toBeInTheDocument();
  });

  it("the hero's primary action starts the first item → /a/:slug/new", () => {
    renderDash();
    // The hero adds a second "Start Investigation" CTA alongside the sidebar's;
    // both point at the create route.
    const links = screen.getAllByRole("link", { name: "Start Investigation" });
    expect(links.length).toBeGreaterThanOrEqual(2);
    for (const l of links) expect(l).toHaveAttribute("href", "/a/rca/new");
  });
});
