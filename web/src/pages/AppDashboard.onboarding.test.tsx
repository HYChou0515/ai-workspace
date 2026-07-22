// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
    onboarding: {
      version: "1",
      title: "Welcome to Root Cause Analysis",
      intro: "Investigate failures end to end.",
      points: [{ title: "Add your evidence", body: "Upload logs and data." }],
    },
  }),
  useAppItems: () => ({
    items: [
      { resource_id: "rca-investigation/1", title: "Oven drift", owner: "u", severity: "P1", status: "triaging", product: "MX-7" },
    ],
    isPending: false,
  }),
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

describe("AppDashboard onboarding", () => {
  it("auto-shows the App's welcome teaching on first entry", () => {
    renderDash();
    expect(
      screen.getByRole("dialog", { name: /welcome to root cause analysis/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("Add your evidence")).toBeInTheDocument();
  });

  it("'Don't show again' stops the auto-popup, but the ? reopens it", () => {
    renderDash();
    fireEvent.click(screen.getByRole("button", { name: /don't show again/i }));
    cleanup();

    renderDash();
    expect(screen.queryByRole("dialog", { name: /welcome to root cause analysis/i })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /about root cause analysis/i }));
    expect(
      screen.getByRole("dialog", { name: /welcome to root cause analysis/i }),
    ).toBeInTheDocument();
  });
});
