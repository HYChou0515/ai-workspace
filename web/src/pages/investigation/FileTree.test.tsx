// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type FileService, FileServiceProvider, investigationFileService } from "../../api/fileService";
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
    <FileServiceProvider value={investigationFileService("rca", "inv")}>
      <DialogProvider>
        <FileTree
          files={opts.files ?? files}
          dirs={opts.dirs ?? []}
          activePath={null}
          onOpen={onOpen}
        />
      </DialogProvider>
    </FileServiceProvider>,
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
        { path: "/notes.md", size: 1 },
        { path: "/dst/notes.md", size: 1 },
      ],
    });
    // drop the root notes.md onto the /dst folder, which already has one
    fireEvent.drop(screen.getByText("dst"), dropPayload(["/notes.md"]));
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

describe("<FileTree /> reindex (#98)", () => {
  it("reindexes the whole selection from the context menu", async () => {
    const user = userEvent.setup();
    const onReindex = vi.fn();
    render(
      <FileServiceProvider value={investigationFileService("rca", "inv")}>
        <DialogProvider>
          <FileTree files={files} dirs={[]} activePath={null} onOpen={vi.fn()} onReindex={onReindex} />
        </DialogProvider>
      </FileServiceProvider>,
    );
    await user.click(screen.getByText("a.md"));
    await user.keyboard("{Control>}");
    await user.click(screen.getByText("b.md"));
    await user.keyboard("{/Control}");
    fireEvent.contextMenu(screen.getByText("b.md"));
    await user.click(await screen.findByRole("button", { name: /^reindex$/i }));
    expect(onReindex).toHaveBeenCalledTimes(1);
    expect([...onReindex.mock.calls[0]![0]].sort()).toEqual(["/a.md", "/b.md"]);
  });

  it("a multi-selection menu shows only selection-wide actions (no Rename/New/Copy)", async () => {
    const user = userEvent.setup();
    const onReindex = vi.fn();
    render(
      <FileServiceProvider value={investigationFileService("rca", "inv")}>
        <DialogProvider>
          <FileTree files={files} dirs={[]} activePath={null} onOpen={vi.fn()} onReindex={onReindex} />
        </DialogProvider>
      </FileServiceProvider>,
    );
    await user.click(screen.getByText("a.md"));
    await user.keyboard("{Control>}");
    await user.click(screen.getByText("b.md"));
    await user.keyboard("{/Control}");
    fireEvent.contextMenu(screen.getByText("b.md"));
    const menu = screen.getByTestId("tree-context-menu");
    // selection-wide actions stay …
    expect(within(menu).getByRole("button", { name: /delete/i })).toBeInTheDocument();
    expect(within(menu).getByRole("button", { name: /^reindex$/i })).toBeInTheDocument();
    // … single-only actions are hidden
    expect(within(menu).queryByRole("button", { name: /rename/i })).not.toBeInTheDocument();
    expect(within(menu).queryByRole("button", { name: /new file/i })).not.toBeInTheDocument();
    expect(within(menu).queryByRole("button", { name: /copy path/i })).not.toBeInTheDocument();
  });

  it("offers no Reindex item when the service can't reindex (onReindex omitted)", () => {
    renderTree();
    fireEvent.contextMenu(screen.getByText("a.md"));
    expect(screen.queryByRole("button", { name: /^reindex$/i })).not.toBeInTheDocument();
  });
});

describe("<FileTree /> context menu position (#99)", () => {
  it("opens upward when the click is near the viewport bottom", () => {
    Object.defineProperty(window, "innerHeight", { value: 300, configurable: true });
    renderTree();
    fireEvent.contextMenu(screen.getByText("a.md"), { clientX: 10, clientY: 285 });
    const menu = screen.getByTestId("tree-context-menu");
    // anchored from the bottom (opens upward), not pinned at top:285 which would
    // run off-screen
    expect(menu.style.top).toBe("");
    expect(menu.style.bottom).not.toBe("");
  });

  it("anchors at the click when there's room below", () => {
    Object.defineProperty(window, "innerHeight", { value: 1000, configurable: true });
    renderTree();
    fireEvent.contextMenu(screen.getByText("a.md"), { clientX: 10, clientY: 50 });
    const menu = screen.getByTestId("tree-context-menu");
    expect(menu.style.top).toBe("50px");
  });
});

describe("<FileTree /> download (#247)", () => {
  function spyService(over: Partial<FileService>): FileService {
    return { ...investigationFileService("rca", "inv"), ...over };
  }
  function renderWith(svc: FileService, fs: FileInfo[] = files) {
    render(
      <FileServiceProvider value={svc}>
        <DialogProvider>
          <FileTree files={fs} dirs={[]} activePath={null} onOpen={vi.fn()} />
        </DialogProvider>
      </FileServiceProvider>,
    );
  }

  it("toolbar Download all confirms, then prepares + streams the root zip", async () => {
    const user = userEvent.setup();
    const prepareDirDownload = vi.fn(async () => ({
      download_id: "d1",
      filename: "inv.zip",
      size: 5,
    }));
    const dirDownloadUrl = vi.fn(() => "/dl/d1");
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderWith(spyService({ prepareDirDownload, dirDownloadUrl }));

    await user.click(screen.getByRole("button", { name: /download all/i }));
    await user.click(screen.getByRole("button", { name: "Download" })); // confirm
    await waitFor(() => expect(prepareDirDownload).toHaveBeenCalledWith(""));
    expect(dirDownloadUrl).toHaveBeenCalledWith("d1", "");
    expect(click).toHaveBeenCalled();
    click.mockRestore();
  });

  it("cancelling the Download all prompt downloads nothing", async () => {
    const user = userEvent.setup();
    const prepareDirDownload = vi.fn(async () => ({ download_id: "x", filename: "x.zip", size: 0 }));
    renderWith(spyService({ prepareDirDownload }));
    await user.click(screen.getByRole("button", { name: /download all/i }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(prepareDirDownload).not.toHaveBeenCalled();
  });

  it("context-menu Download on a file links its bytes with a clean name", async () => {
    const user = userEvent.setup();
    const fileDownloadUrl = vi.fn((p: string) => `/files${p}`);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderWith(spyService({ fileDownloadUrl }));

    fireEvent.contextMenu(screen.getByText("a.md"));
    await user.click(screen.getByText("Download"));
    expect(fileDownloadUrl).toHaveBeenCalledWith("/a.md");
    const a = click.mock.instances[0] as HTMLAnchorElement;
    expect(a.getAttribute("download")).toBe("a.md"); // basename, not the URL tail
    click.mockRestore();
  });

  it("context-menu Download on a folder prepares its subtree zip", async () => {
    const user = userEvent.setup();
    const prepareDirDownload = vi.fn(async () => ({
      download_id: "d2",
      filename: "mydir.zip",
      size: 3,
    }));
    const dirDownloadUrl = vi.fn(() => "/dl/d2");
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderWith(spyService({ prepareDirDownload, dirDownloadUrl }), [{ path: "/mydir/x.md", size: 1 }]);

    fireEvent.contextMenu(screen.getByText("mydir"));
    await user.click(screen.getByText("Download"));
    await waitFor(() => expect(prepareDirDownload).toHaveBeenCalledWith("/mydir"));
    expect(dirDownloadUrl).toHaveBeenCalledWith("d2", "/mydir");
    click.mockRestore();
  });

  it("hides the download UI when the service can't download", () => {
    const base = investigationFileService("rca", "inv");
    renderWith(spyService({ caps: { ...base.caps, download: false } }));
    expect(screen.queryByRole("button", { name: /download all/i })).toBeNull();
    fireEvent.contextMenu(screen.getByText("a.md"));
    expect(screen.queryByText("Download")).toBeNull();
  });
});

describe("<FileTree /> upload target", () => {
  function spyService(over: Partial<FileService>): FileService {
    return { ...investigationFileService("rca", "inv"), ...over };
  }
  function renderWith(svc: FileService, files: FileInfo[]) {
    render(
      <FileServiceProvider value={svc}>
        <DialogProvider>
          <FileTree files={files} dirs={[]} activePath={null} onOpen={vi.fn()} />
        </DialogProvider>
      </FileServiceProvider>,
    );
  }
  const filesInput = () =>
    document.querySelector('input[type="file"]:not([webkitdirectory])') as HTMLInputElement;

  it("toolbar upload lands the file inside the selected folder", async () => {
    const user = userEvent.setup();
    const writeFile = vi.fn(async (_path: string, _body: string | Blob | ArrayBuffer) => {});
    renderWith(spyService({ writeFile }), [{ path: "/mydir/a.md", size: 1 }]);
    await user.click(screen.getByText("mydir")); // anchor the folder
    const file = new File(["x"], "up.md", { type: "text/markdown" });
    fireEvent.change(filesInput(), { target: { files: [file] } });
    await waitFor(() => expect(writeFile).toHaveBeenCalled());
    expect(writeFile.mock.calls[0]![0]).toBe("/mydir/up.md");
  });

  it("a folder's context menu uploads files into that folder", async () => {
    const user = userEvent.setup();
    const writeFile = vi.fn(async (_path: string, _body: string | Blob | ArrayBuffer) => {});
    renderWith(spyService({ writeFile }), [{ path: "/mydir/a.md", size: 1 }]);
    fireEvent.contextMenu(screen.getByText("mydir"));
    await user.click(await screen.findByText(/upload files here/i));
    const file = new File(["x"], "up.md", { type: "text/markdown" });
    fireEvent.change(filesInput(), { target: { files: [file] } });
    await waitFor(() => expect(writeFile).toHaveBeenCalled());
    expect(writeFile.mock.calls[0]![0]).toBe("/mydir/up.md");
  });

  it("toolbar upload with nothing selected lands the file at the root", async () => {
    const writeFile = vi.fn(async (_path: string, _body: string | Blob | ArrayBuffer) => {});
    renderWith(spyService({ writeFile }), [{ path: "/a.md", size: 1 }]);
    const file = new File(["x"], "up.md", { type: "text/markdown" });
    fireEvent.change(filesInput(), { target: { files: [file] } });
    await waitFor(() => expect(writeFile).toHaveBeenCalled());
    expect(writeFile.mock.calls[0]![0]).toBe("/up.md");
  });

  it("uploads a file larger than the old 8 MB cap (no client-side skip) (#219)", async () => {
    const writeFile = vi.fn(async (_path: string, _body: string | Blob | ArrayBuffer) => {});
    renderWith(spyService({ writeFile }), [{ path: "/a.md", size: 1 }]);
    const big = new File([new Uint8Array(9 * 1024 * 1024)], "big.bin");
    fireEvent.change(filesInput(), { target: { files: [big] } });
    await waitFor(() => expect(writeFile).toHaveBeenCalled());
    expect(writeFile.mock.calls[0]![0]).toBe("/big.bin");
  });

  it("alerts and keeps going when the server rejects an upload (#219)", async () => {
    const alertSpy = vi.fn();
    vi.stubGlobal("alert", alertSpy);
    const writeFile = vi.fn(async () => {
      throw new Error("413");
    });
    renderWith(spyService({ writeFile }), [{ path: "/a.md", size: 1 }]);
    const file = new File(["x"], "up.md", { type: "text/markdown" });
    fireEvent.change(filesInput(), { target: { files: [file] } });
    await waitFor(() => expect(alertSpy).toHaveBeenCalled());
    expect(alertSpy.mock.calls[0]![0]).toMatch(/size limit/i);
    vi.unstubAllGlobals();
  });
});

describe("<FileTree /> external drop + paste (#364)", () => {
  function spyService(over: Partial<FileService>): FileService {
    return { ...investigationFileService("rca", "inv"), ...over };
  }
  function renderWith(svc: FileService, fileList: FileInfo[]) {
    render(
      <FileServiceProvider value={svc}>
        <DialogProvider>
          <FileTree files={fileList} dirs={[]} activePath={null} onOpen={vi.fn()} />
        </DialogProvider>
      </FileServiceProvider>,
    );
  }
  const external = (files: File[]) => ({
    dataTransfer: { types: ["Files"], files, items: [], getData: () => "" },
  });
  const body = () => screen.getByTestId("file-tree-body");

  it("drops an external file onto the empty tree into the anchored folder", async () => {
    const user = userEvent.setup();
    const writeFile = vi.fn(async (_path: string, _body?: string | Blob | ArrayBuffer) => {});
    renderWith(spyService({ writeFile }), [{ path: "/mydir/a.md", size: 1 }]);
    await user.click(screen.getByText("mydir"));
    fireEvent.drop(body(), external([new File(["x"], "up.png", { type: "image/png" })]));
    await waitFor(() => expect(writeFile).toHaveBeenCalled());
    expect(writeFile.mock.calls[0]![0]).toBe("/mydir/up.png");
  });

  it("drops an external file onto a folder row into that folder", async () => {
    const writeFile = vi.fn(async (_path: string, _body?: string | Blob | ArrayBuffer) => {});
    renderWith(spyService({ writeFile }), [{ path: "/mydir/a.md", size: 1 }]);
    fireEvent.drop(screen.getByText("mydir"), external([new File(["x"], "up.csv")]));
    await waitFor(() => expect(writeFile).toHaveBeenCalled());
    expect(writeFile.mock.calls[0]![0]).toBe("/mydir/up.csv");
  });

  it("recurses a dropped external folder, preserving structure", async () => {
    const writeFile = vi.fn(async (_path: string, _body?: string | Blob | ArrayBuffer) => {});
    renderWith(spyService({ writeFile }), [{ path: "/a.md", size: 1 }]);
    let handed = false;
    const dir = {
      isFile: false,
      isDirectory: true,
      name: "data",
      createReader: () => ({
        readEntries: (cb: (e: unknown[]) => void) => {
          if (handed) return cb([]);
          handed = true;
          cb([
            {
              isFile: true,
              isDirectory: false,
              name: "n.csv",
              file: (ok: (f: File) => void) => ok(new File(["x"], "n.csv")),
            },
          ]);
        },
      }),
    };
    fireEvent.drop(body(), {
      dataTransfer: {
        types: ["Files"],
        files: [],
        items: [{ kind: "file", webkitGetAsEntry: () => dir }],
        getData: () => "",
      },
    });
    await waitFor(() => expect(writeFile).toHaveBeenCalled());
    expect(writeFile.mock.calls[0]![0]).toBe("/data/n.csv");
  });

  it("pastes a file into the focused tree, into the anchored folder", async () => {
    const user = userEvent.setup();
    const writeFile = vi.fn(async (_path: string, _body?: string | Blob | ArrayBuffer) => {});
    renderWith(spyService({ writeFile }), [{ path: "/mydir/a.md", size: 1 }]);
    await user.click(screen.getByText("mydir"));
    fireEvent.paste(body(), {
      clipboardData: {
        items: [
          {
            kind: "file",
            type: "image/png",
            getAsFile: () => new File(["x"], "image.png", { type: "image/png" }),
          },
        ],
        files: [],
      },
    });
    await waitFor(() => expect(writeFile).toHaveBeenCalled());
    expect(writeFile.mock.calls[0]![0]).toMatch(/^\/mydir\/pasted-image-\d+\.png$/);
  });

  it("does not upload when plain text is pasted", () => {
    const writeFile = vi.fn(async (_path: string, _body?: string | Blob | ArrayBuffer) => {});
    renderWith(spyService({ writeFile }), [{ path: "/a.md", size: 1 }]);
    fireEvent.paste(body(), {
      clipboardData: { items: [{ kind: "string", type: "text/plain", getAsFile: () => null }], files: [] },
    });
    expect(writeFile).not.toHaveBeenCalled();
  });

  it("still moves an internally-dragged file (regression)", async () => {
    const moveFile = vi.fn(async (_src: string, _dst: string) => {});
    renderWith(spyService({ moveFile }), [
      { path: "/notes.md", size: 1 },
      { path: "/dst/x.md", size: 1 },
    ]);
    fireEvent.drop(screen.getByText("dst"), {
      dataTransfer: {
        types: ["application/x-rca-file"],
        getData: (t: string) =>
          t === "application/x-rca-file" ? JSON.stringify({ paths: ["/notes.md"] }) : "",
        files: [],
        items: [],
      },
    });
    await waitFor(() => expect(moveFile).toHaveBeenCalled());
    expect(moveFile.mock.calls[0]![0]).toBe("/notes.md");
    expect(moveFile.mock.calls[0]![1]).toBe("/dst/notes.md");
  });
});

describe("<FileTree /> sticky header (#346)", () => {
  it("pins the Files header to the top so it survives scrolling the tree", () => {
    renderTree();
    const header = screen.getByTestId("file-tree-header");
    // Sticky-at-top keeps the title + action icons visible no matter how far
    // the file list scrolls inside its container.
    expect(header).toHaveStyle({ position: "sticky" });
    expect(header.style.top).toMatch(/^0(px)?$/);
  });
});
