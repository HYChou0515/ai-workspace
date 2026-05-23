// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileInfo } from "../../api/types";
import { DialogProvider } from "../../components/Dialog";
import { FileTree } from "./FileTree";

afterEach(cleanup);

const files: FileInfo[] = [
  { path: "/a.md", size: 1 },
  { path: "/b.md", size: 1 },
  { path: "/c.md", size: 1 },
];

function renderTree(onOpen = vi.fn()) {
  render(
    <DialogProvider>
      <FileTree investigationId="inv" files={files} dirs={[]} activePath={null} onOpen={onOpen} />
    </DialogProvider>,
  );
  return { onOpen };
}

describe("<FileTree /> multi-select", () => {
  it("plain click opens the file", async () => {
    const user = userEvent.setup();
    const { onOpen } = renderTree();
    await user.click(screen.getByText("a.md"));
    expect(onOpen).toHaveBeenCalledWith("/a.md", { preview: true });
  });

  it("ctrl-click builds a multi-selection that bulk delete acts on", async () => {
    const user = userEvent.setup();
    renderTree();

    await user.click(screen.getByText("a.md"));
    await user.keyboard("{Control>}");
    await user.click(screen.getByText("b.md"));
    await user.keyboard("{/Control}");

    // right-click within the selection → Delete → modal lists the count
    fireEvent.contextMenu(screen.getByText("b.md"));
    await user.click(await screen.findByRole("button", { name: /^delete$/i }));
    expect(await screen.findByText(/delete 2 items/i)).toBeInTheDocument();
  });
});
