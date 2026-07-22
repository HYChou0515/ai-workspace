// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../test/queryWrapper";

afterEach(cleanup);
beforeEach(() => localStorage.clear());

import { Launcher } from "./Launcher";
import { BreadcrumbProvider, useBreadcrumbTrail } from "../hooks/breadcrumbs";
import { useApps } from "../hooks/useResources";

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

vi.mock("../hooks/useCurrentUser", () => ({
  useCurrentUser: () => "alice",
  useCurrentUserState: () => ({ id: "alice", ready: true }),
}));

vi.mock("../hooks/useResources", () => ({
  useApps: vi.fn(() => [
    {
      slug: "rca",
      title: "Root Cause Analysis",
      description: "Find root causes.",
      icon: "flame",
      color: "#F0502E",
    },
    {
      slug: "yield",
      title: "Yield Tracking",
      description: "Track yield trends.",
      icon: "bug",
      color: "#2D6CC9",
    },
  ]),
}));

function renderLauncher() {
  return render(
    <QueryWrap>
      <MemoryRouter>
        <Launcher />
      </MemoryRouter>
    </QueryWrap>,
  );
}

describe("Launcher", () => {
  it("renders a card per App, each linking to /a/:slug with its title + description", () => {
    renderLauncher();
    const rca = screen.getByRole("link", { name: /Root Cause Analysis/ });
    expect(rca).toHaveAttribute("href", "/a/rca");
    expect(screen.getByText("Find root causes.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Yield Tracking/ })).toHaveAttribute(
      "href",
      "/a/yield",
    );
  });

  it("renders a fixed Knowledge Base link card → /kb (KB is not an App)", () => {
    renderLauncher();
    expect(screen.getByRole("link", { name: /知識庫/ })).toHaveAttribute("href", "/kb");
  });

  it("guides an empty workspace: explains apps are team-set-up + keeps the KB reachable (#170)", () => {
    const mock = vi.mocked(useApps);
    const prev = mock.getMockImplementation();
    mock.mockReturnValue([]); // persistent — survives the modal's re-render
    try {
      renderLauncher();
      // a real empty-state with a next step, not a bare "尚無應用程式。" line
      expect(screen.getByText(/由團隊設定/)).toBeInTheDocument();
      // the knowledge base is still one click away
      expect(screen.getByRole("link", { name: /知識庫/ })).toHaveAttribute("href", "/kb");
    } finally {
      mock.mockReset();
      if (prev) mock.mockImplementation(prev);
    }
  });

  it("#160: does not print the raw /a/:slug route on the card face", () => {
    renderLauncher();
    expect(screen.queryByText("/a/rca")).not.toBeInTheDocument();
    expect(screen.queryByText("/a/yield")).not.toBeInTheDocument();
  });

  it("auto-shows the platform welcome on first visit", () => {
    renderLauncher();
    expect(screen.getByRole("dialog", { name: /welcome to your workspace/i })).toBeInTheDocument();
  });

  it("'Don't show again' stops the auto-popup, but the ? reopens it", () => {
    renderLauncher();
    fireEvent.click(screen.getByRole("button", { name: /don't show again/i }));
    cleanup();

    renderLauncher(); // a fresh visit — no longer auto-shown
    expect(screen.queryByRole("dialog", { name: /welcome to your workspace/i })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /about this workspace/i }));
    expect(screen.getByRole("dialog", { name: /welcome to your workspace/i })).toBeInTheDocument();
  });

  it("publishes a Home breadcrumb (the launcher is the home leaf)", () => {
    render(
      <MemoryRouter>
        <BreadcrumbProvider>
          <Launcher />
          <TrailProbe />
        </BreadcrumbProvider>
      </MemoryRouter>,
    );
    const items = screen.getByTestId("trail").querySelectorAll("li");
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent("Home");
    // leaf → no link target
    expect(items[0].getAttribute("data-to")).toBe("");
  });

  it("drops its own redundant title header (the global bar already brands 'Workspace')", () => {
    renderLauncher();
    expect(screen.queryByText("Workspace")).toBeNull();
  });
});
