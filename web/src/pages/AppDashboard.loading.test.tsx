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

// #225: the manifest has loaded but the items request is still in flight. The
// list is momentarily empty — but that's "we don't know yet", not "no items".
// The dashboard must show a loading skeleton, NOT the first-user "create your
// first" hero (which would otherwise flash a misleading create button).
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
  useAppItems: () => ({ items: [], isPending: true }),
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

describe("AppDashboard items still loading (#225)", () => {
  it("shows a loading skeleton, not the 'create your first' hero", () => {
    renderDash();
    // The first-user hero (and its create CTA) must NOT appear while we still
    // don't know whether the App has items.
    expect(screen.queryByText(/no investigations yet/i)).toBeNull();
    // The skeleton container marks itself busy so the wait reads as content
    // arriving rather than an empty App.
    expect(screen.getByTestId("page-app-dashboard")).toHaveAttribute("aria-busy", "true");
  });
});
