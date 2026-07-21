// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../test/queryWrapper";
import { AppDashboard } from "./AppDashboard";

afterEach(cleanup);
beforeEach(() => localStorage.clear());

vi.mock("../hooks/useCurrentUser", () => ({ useCurrentUser: () => "me" }));
vi.mock("../hooks/useUsers", () => ({
  useUsers: () => [],
  useUser: (id: string) => ({ id, name: id, section: "", email: "", photo_url: null }),
}));

const ITEMS = [
  // Owned by me, explicitly opened up — the thing an owner is scanning FOR.
  { resource_id: "i/1", title: "Opened up", owner: "me", created_by: "me", permission: { visibility: "public" } },
  // Owned by me, shared with named people only.
  { resource_id: "i/2", title: "Shared narrowly", owner: "me", created_by: "me", permission: { visibility: "restricted", read_meta: ["user:bob"] } },
  // Owned by me, still closed. New items default to private.
  { resource_id: "i/3", title: "Still closed", owner: "me", created_by: "me", permission: { visibility: "private" } },
  // A legacy row that predates the permission model: absent ≡ public, and that
  // is exactly what the owner needs to notice — it IS reachable by everyone.
  { resource_id: "i/4", title: "Legacy", owner: "me", created_by: "me" },
];

vi.mock("../hooks/useResources", () => ({
  useAppManifest: vi.fn(() => ({
    slug: "rca",
    title: "Root Cause Analysis",
    description: "",
    icon: "flame",
    color: "#F0502E",
    item: { noun: "Investigation", noun_plural: "Investigations", create_label: "Start" },
    layout: { list: ["severity"], breadcrumb: [], statusbar: [] },
    labels: { severity: "Severity" },
    fields: [{ name: "severity", label: "Severity", kind: "select", options: ["P1"] }],
    field_styles: {},
    lifecycle: { status_field: "status", closing_states: ["resolved"] },
    resource_route: "/rca-investigation",
    function: { workspace: true, sandbox: true, terminal: true },
    agent: { picker: [] },
    default_profile: "default",
  })),
  useAppItems: vi.fn(() => ({ items: ITEMS, isPending: false })),
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

const rowFor = (title: string) =>
  screen.getAllByRole("row").find((r) => within(r).queryByText(title)) as HTMLElement;

describe("#578 — access is visible on the item table", () => {
  it("names the access of every item the owner can open", () => {
    // The whole point: scan DOWN the list and see which are already open. The
    // only access signal before this was the locked title for items you CANNOT
    // open, so an owner had to open each share dialog one by one.
    renderDash();

    expect(within(rowFor("Opened up")).getByText("Public")).toBeInTheDocument();
    expect(within(rowFor("Shared narrowly")).getByText("Restricted")).toBeInTheDocument();
    expect(within(rowFor("Still closed")).getByText("Private")).toBeInTheDocument();
  });

  it("shows a permission-less legacy item as Public, because it is", () => {
    // Absent ≡ public on the backend (WorkItemBase.permission docstring). Showing
    // it blank would hide exactly the row an owner most needs to notice.
    renderDash();

    expect(within(rowFor("Legacy")).getByText("Public")).toBeInTheDocument();
  });

  it("gives the column a header so the values are not a mystery", () => {
    renderDash();

    expect(screen.getByRole("columnheader", { name: "Access" })).toBeInTheDocument();
  });
});
