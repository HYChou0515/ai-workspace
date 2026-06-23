// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

import { Launcher } from "./Launcher";

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
    <MemoryRouter>
      <Launcher />
    </MemoryRouter>,
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

  it("#160: does not print the raw /a/:slug route on the card face", () => {
    renderLauncher();
    expect(screen.queryByText("/a/rca")).not.toBeInTheDocument();
    expect(screen.queryByText("/a/yield")).not.toBeInTheDocument();
  });
});
