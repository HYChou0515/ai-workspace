// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../test/queryWrapper";

afterEach(cleanup);
beforeEach(() => localStorage.clear());

import { Launcher } from "./Launcher";

vi.mock("../hooks/useCurrentUser", () => ({ useCurrentUser: () => "alice" }));

vi.mock("../hooks/useResources", () => ({
  useApps: () => [
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
  ],
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
    expect(screen.getByRole("link", { name: /Knowledge Base/ })).toHaveAttribute("href", "/kb");
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
});
