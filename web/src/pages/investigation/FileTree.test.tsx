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

function renderTree(onOpen = vi.fn(), opts: { files?: FileInfo[]; dirs?: string[] } = {}) {
  render(
    <DialogProvider>
      <FileTree
        investigationId="inv"
        files={opts.files ?? files}
        dirs={opts.dirs ?? []}
        activePath={null}
        onOpen={onOpen}
      />
    </DialogProvider>,
  );
  return { onOpen };
}

function dropPayload(paths: string[]) {
  return {
    dataTransfer: {
      getData: (t: string) => (t === "application/x-rca-file" ? JSON.stringify({ paths }) : ""),
      types: ["application/x-rca-file"],
    },
  };
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

  it("prompts to replace when a move collides with an existing name", async () => {
    renderTree(vi.fn(), {
      files: [
        { path: "/5-why.md", size: 1 },
        { path: "/dst/5-why.md", size: 1 },
      ],
    });
    // drop the root 5-why.md onto the /dst folder, which already has one
    fireEvent.drop(screen.getByText("dst"), dropPayload(["/5-why.md"]));
    expect(await screen.findByText(/already exists/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /replace/i })).toBeInTheDocument();
  });

  it("prompts to replace when a new file name collides", async () => {
    const user = userEvent.setup();
    renderTree();
    await user.click(screen.getByTitle("New file"));
    await user.type(await screen.findByPlaceholderText("file name"), "a.md{Enter}");
    expect(await screen.findByText(/already exists/i)).toBeInTheDocument();
  });

  it("prompts to replace when a rename collides with a sibling", async () => {
    const user = userEvent.setup();
    renderTree();
    fireEvent.contextMenu(screen.getByText("a.md"));
    await user.click(await screen.findByRole("button", { name: /rename/i }));
    const input = await screen.findByDisplayValue("a.md");
    await user.clear(input);
    await user.type(input, "b.md{Enter}");
    expect(await screen.findByText(/already exists/i)).toBeInTheDocument();
  });
});
