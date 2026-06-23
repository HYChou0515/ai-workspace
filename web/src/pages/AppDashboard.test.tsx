// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BreadcrumbProvider, useBreadcrumbTrail } from "../hooks/breadcrumbs";
import { QueryWrap } from "../test/queryWrapper";
import { AppDashboard } from "./AppDashboard";

afterEach(cleanup);
beforeEach(() => localStorage.clear());

function TrailProbe() {
  const trail = useBreadcrumbTrail();
  return (
    <ul data-testid="trail">
      {trail.map((c, i) => (
        <li key={i} data-to={c.to ?? ""}>
          {c.label}
        </li>
      ))}
    </ul>
  );
}

// Owner/current-user resolution is its own concern (useUsers tests); stub it so
// the dashboard test stays hermetic and doesn't reach the network.
vi.mock("../hooks/useCurrentUser", () => ({ useCurrentUser: () => "default-user" }));
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
    fields: [
      { name: "severity", label: "Severity", kind: "select", options: ["P1", "P2", "P3"] },
      {
        name: "status",
        label: "Status",
        kind: "select",
        options: ["triaging", "awaiting_review", "resolved", "abandoned"],
      },
      { name: "product", label: "Product", kind: "text" },
    ],
    field_styles: {
      severity: { P1: "err", P2: "warn", P3: "ok" },
      status: { triaging: "warn", awaiting_review: "info", resolved: "ok", abandoned: "muted" },
    },
    lifecycle: { status_field: "status", closing_states: ["resolved", "abandoned"] },
    resource_route: "/rca-investigation",
    function: { workspace: true, sandbox: true, terminal: true },
    agent: { picker: [] },
    default_profile: "default",
  }),
  useAppItems: () => [
    {
      resource_id: "rca-investigation/1",
      title: "Oven drift",
      owner: "u",
      severity: "P1",
      status: "triaging",
      product: "MX-7",
      topics: ["Reflow"],
    },
    {
      resource_id: "rca-investigation/2",
      title: "Sealed batch",
      owner: "u",
      severity: "P2",
      status: "resolved",
      product: "Display",
      topics: ["Panel"],
    },
  ],
}));

function renderDashAt(entry: string, extra?: React.ReactNode) {
  return render(
    <QueryWrap>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route
            path="/a/:slug"
            element={
              <>
                <AppDashboard />
                {extra}
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryWrap>,
  );
}

function renderDash() {
  return renderDashAt("/a/rca");
}

describe("AppDashboard", () => {
  it("shows the App brand + a create link → /a/:slug/new", () => {
    renderDash();
    expect(screen.getByText("Root Cause Analysis")).toBeInTheDocument(); // sidebar brand
    expect(screen.getByRole("link", { name: "Start Investigation" })).toHaveAttribute(
      "href",
      "/a/rca/new",
    );
  });

  it("summarizes open vs critical items in the page heading", () => {
    renderDash();
    // 1 open (Oven drift, triaging); 1 critical (P1 → err tone)
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent(/1 open/);
    expect(heading).toHaveTextContent(/1 critical/);
  });

  it("renders status tabs with counts; clicking a closed status reveals its items", () => {
    renderDash();
    const tabs = screen.getByTestId("dash-tabs");
    expect(within(tabs).getByRole("button", { name: /Triaging/ })).toBeInTheDocument();
    // default "All" view = open only → the resolved item is hidden
    expect(screen.queryByRole("link", { name: /Sealed batch/ })).not.toBeInTheDocument();
    fireEvent.click(within(tabs).getByRole("button", { name: /^Resolved/ }));
    expect(screen.getByRole("link", { name: /Sealed batch/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Oven drift/ })).not.toBeInTheDocument();
  });

  it("renders a table: column headers from layout + a row linking to the item with toned chips", () => {
    renderDash();
    expect(screen.getByRole("columnheader", { name: /Severity/ })).toBeInTheDocument();
    const table = screen.getByTestId("dash-items");
    expect(within(table).getByRole("link", { name: /Oven drift/ })).toHaveAttribute(
      "href",
      "/a/rca/rca-investigation%2F1",
    );
    expect(within(table).getByText("P1")).toBeInTheDocument(); // severity chip
    expect(within(table).getByText("triaging")).toBeInTheDocument(); // status chip
    expect(within(table).getByText("MX-7")).toBeInTheDocument(); // product
  });

  it("lists Topics in the sidebar derived from the items' topics", () => {
    renderDash();
    const sidebar = screen.getByTestId("dash-sidebar");
    expect(within(sidebar).getByText("Reflow")).toBeInTheDocument();
    expect(within(sidebar).getByText("Panel")).toBeInTheDocument();
  });

  it("narrows the list via the severity filter in the filter strip", () => {
    renderDash();
    fireEvent.change(screen.getByLabelText("Filter by severity"), { target: { value: "P2" } });
    // Oven drift is P1 → filtered out
    expect(screen.queryByRole("link", { name: /Oven drift/ })).not.toBeInTheDocument();
  });

  it("pins an item from its row and reflects it in the Pinned filter", () => {
    renderDash();
    fireEvent.click(screen.getByRole("button", { name: /Pin Oven drift/ }));
    expect(screen.getByRole("button", { name: /Unpin Oven drift/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Pinned/ }));
    const list = screen.getByTestId("dash-items");
    expect(within(list).getByRole("link", { name: /Oven drift/ })).toBeInTheDocument();
  });

  it("publishes a Home › {App title} breadcrumb", () => {
    render(
      <QueryWrap>
        <MemoryRouter initialEntries={["/a/rca"]}>
          <Routes>
            <Route
              path="/a/:slug"
              element={
                <BreadcrumbProvider>
                  <AppDashboard />
                  <TrailProbe />
                </BreadcrumbProvider>
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryWrap>,
    );
    const items = screen.getByTestId("trail").querySelectorAll("li");
    expect([...items].map((li) => li.textContent)).toEqual(["Home", "Root Cause Analysis"]);
    expect(items[0].getAttribute("data-to")).toBe("/");
    expect(items[1].getAttribute("data-to")).toBe("");
  });

  it("no longer carries its own 'All apps' home icon (the global bar navigates)", () => {
    renderDash();
    expect(screen.queryByRole("link", { name: /All apps/i })).toBeNull();
  });

  it("initializes the topic filter from ?topic= (breadcrumb chip deep-link)", () => {
    // Oven drift carries topic "Reflow"; deep-linking ?topic=Panel must hide it.
    renderDashAt("/a/rca?topic=Panel");
    expect(screen.queryByRole("link", { name: /Oven drift/ })).not.toBeInTheDocument();

    cleanup();
    renderDashAt("/a/rca?topic=Reflow");
    expect(screen.getByRole("link", { name: /Oven drift/ })).toBeInTheDocument();
  });
});
