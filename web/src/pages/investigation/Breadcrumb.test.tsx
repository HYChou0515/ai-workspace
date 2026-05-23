// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileInfo } from "../../api/types";
import { Breadcrumb } from "./InvestigationShell";

afterEach(cleanup);

const files: FileInfo[] = [
  { path: "/brief.md", size: 0 },
  { path: "/data/meta.json", size: 0 },
  { path: "/data/raw/spc.csv", size: 0 },
  { path: "/data/raw/spc2.csv", size: 0 },
];

describe("<Breadcrumb />", () => {
  it("renders a placeholder when nothing is open", () => {
    render(<Breadcrumb activeTab={null} files={files} onOpen={vi.fn()} />);
    expect(screen.getByText(/no file open/i)).toBeInTheDocument();
  });

  it("opens a sibling file from the active file's segment dropdown", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<Breadcrumb activeTab="/data/raw/spc.csv" files={files} onOpen={onOpen} />);

    await user.click(screen.getByRole("button", { name: "spc.csv" }));
    await user.click(await screen.findByText("spc2.csv"));
    expect(onOpen).toHaveBeenCalledWith("/data/raw/spc2.csv");
  });

  it("lists an ancestor's siblings and opens one", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<Breadcrumb activeTab="/data/raw/spc.csv" files={files} onOpen={onOpen} />);

    // the "data" crumb sits at root level → its dropdown lists /brief.md
    await user.click(screen.getByRole("button", { name: "data" }));
    await user.click(await screen.findByText("brief.md"));
    expect(onOpen).toHaveBeenCalledWith("/brief.md");
  });

  it("drills into a folder before opening a file", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<Breadcrumb activeTab="/brief.md" files={files} onOpen={onOpen} />);

    await user.click(screen.getByRole("button", { name: "brief.md" }));
    await user.click(await screen.findByText("data")); // folder, unique here
    await user.click(await screen.findByText("meta.json"));
    expect(onOpen).toHaveBeenCalledWith("/data/meta.json");
  });
});
