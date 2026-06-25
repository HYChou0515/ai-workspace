// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileService } from "../api/fileService";
import type { FileContent, FileInfo } from "../api/types";
import { renderWithQuery } from "../test/queryWrapper";
import { CardDiffReview, CURRENT_PATH, TODO_PATH } from "./CardDiffReview";

// Mock the heavy lazy Monaco diff with a controllable textarea that surfaces both
// sides + the modified-pane onChange, so the modal logic is testable without Monaco.
vi.mock("./MonacoDiffEditor", () => ({
  MonacoDiffEditor: ({
    original,
    modified,
    onChangeModified,
  }: {
    original: string;
    modified: string;
    onChangeModified?: (v: string) => void;
  }) => (
    <div>
      <pre data-testid="diff-original">{original}</pre>
      <textarea
        data-testid="diff-modified"
        defaultValue={modified}
        onChange={(e) => onChangeModified?.(e.target.value)}
      />
    </div>
  ),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** A fake FileService whose two diff files we control + whose writes we observe. */
function fakeSvc(files: Record<string, string>): { svc: FileService; writes: [string, string][] } {
  const writes: [string, string][] = [];
  const listFiles = vi.fn(
    async (): Promise<FileInfo[]> =>
      Object.keys(files).map((p) => ({ path: p, size: files[p]!.length })),
  );
  const readFile = vi.fn(async (path: string): Promise<FileContent> => {
    if (!(path in files)) {
      const err = new Error("404") as Error & { status: number };
      err.status = 404;
      throw err;
    }
    return { kind: "text", path, size: files[path]!.length, text: files[path]!, encoding: "utf-8" };
  });
  const writeFile = vi.fn(async (path: string, body: string | Blob | ArrayBuffer) => {
    writes.push([path, String(body)]);
    files[path] = String(body);
  });
  const svc = {
    scopeId: "it",
    caps: { write: true, create: true, upload: true, delete: true, move: true, copy: true, folders: true },
    listFiles,
    readFile,
    writeFile,
    deleteFile: vi.fn(),
    moveFile: vi.fn(),
    copyFile: vi.fn(),
    mkdir: vi.fn(),
    refreshFiles: vi.fn(),
    fileUrl: (s?: string) => s ?? "",
  } as unknown as FileService;
  return { svc, writes };
}

function render(svc: FileService, onDecide = vi.fn()) {
  renderWithQuery(
    <CardDiffReview
      slug="topic-hub"
      itemId="it1"
      allow={["approve", "reject", "revise"]}
      onDecide={onDecide}
      service={svc}
    />,
  );
  return onDecide;
}

describe("CardDiffReview", () => {
  it("hides the View-changes button when the gate has no proposed-cards file", async () => {
    const { svc } = fakeSvc({ "/memory.todo.md": "x" }); // a different gate's file
    render(svc);
    await waitFor(() => expect(svc.listFiles).toHaveBeenCalled());
    expect(screen.queryByTestId("card-diff-open")).not.toBeInTheDocument();
  });

  it("shows the button and opens a diff of current vs proposed", async () => {
    const { svc } = fakeSvc({
      [TODO_PATH]: "## M4\nkeys: M4\n\nnew def",
      [CURRENT_PATH]: "## Metal 4\nkeys: M4, Metal 4\n\nold def",
    });
    render(svc);
    const open = await screen.findByTestId("card-diff-open");
    fireEvent.click(open);
    // Left pane = the read-only current snapshot; right pane = the editable proposal.
    expect(await screen.findByTestId("diff-original")).toHaveTextContent("old def");
    expect(screen.getByTestId("diff-modified")).toHaveValue("## M4\nkeys: M4\n\nnew def");
  });

  it("saves right-pane edits to the todo file before approving", async () => {
    const { svc, writes } = fakeSvc({
      [TODO_PATH]: "## M4\nkeys: M4\n\nnew def",
      [CURRENT_PATH]: "## Metal 4\nkeys: M4, Metal 4\n\nold def",
    });
    const onDecide = render(svc);
    fireEvent.click(await screen.findByTestId("card-diff-open"));
    const modified = await screen.findByTestId("diff-modified");
    // The human restores the narrowed keys in the diff, then approves.
    fireEvent.change(modified, { target: { value: "## Metal 4\nkeys: M4, Metal 4\n\nnew def" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(writes).toEqual([[TODO_PATH, "## Metal 4\nkeys: M4, Metal 4\n\nnew def"]]),
    );
    expect(onDecide).toHaveBeenCalledWith("approve", undefined);
  });

  it("rejecting decides without writing when nothing was edited", async () => {
    const { svc, writes } = fakeSvc({
      [TODO_PATH]: "## M4\n\nnew def",
      [CURRENT_PATH]: "",
    });
    const onDecide = render(svc);
    fireEvent.click(await screen.findByTestId("card-diff-open"));
    await screen.findByTestId("diff-modified");
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(onDecide).toHaveBeenCalledWith("reject", undefined));
    expect(writes).toEqual([]);
  });

  it("notes when every proposed card is new (nothing to overwrite)", async () => {
    // useT() outside a LocaleProvider renders zh-TW (the default context locale).
    const { svc } = fakeSvc({ [TODO_PATH]: "## M4\n\nnew", [CURRENT_PATH]: "" });
    render(svc);
    fireEvent.click(await screen.findByTestId("card-diff-open"));
    expect(await screen.findByText(/沒有會被覆寫/)).toBeInTheDocument();
  });
});
