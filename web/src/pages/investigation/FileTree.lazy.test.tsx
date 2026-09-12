// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type FileService, FileServiceProvider, investigationFileService } from "../../api/fileService";
import { renderWithQuery } from "../../test/queryWrapper";
import { FileTree } from "./FileTree";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

/** A service whose `listTree` we count: the lazy fetch must go through the
 * same door every other listing does, once per expand, scoped to the folder. */
function lazyService(levels: Record<string, Awaited<ReturnType<FileService["listTree"]>>>) {
  const listTree = vi.fn(async (opts?: { prefix?: string; depth?: number }) => {
    const key = opts?.prefix ?? "/";
    const level = levels[key];
    if (!level) throw new Error(`no fixture for ${key}`);
    return level;
  });
  const svc: FileService = { ...investigationFileService("rca", "inv-lazy"), listTree };
  return { svc, listTree };
}

function renderTree(svc: FileService, unwalked: string[], truncated = false) {
  return renderWithQuery(
    <FileServiceProvider value={svc}>
      <FileTree
        files={[{ path: "/src/a.py", size: 1 }]}
        dirs={["/src", "/node_modules"]}
        unwalked={unwalked}
        truncated={truncated}
        searchable
        activePath={null}
        onOpen={vi.fn()}
      />
    </FileServiceProvider>,
  );
}

describe("<FileTree /> lazy folders", () => {
  it("draws a folder the listing did not enter collapsed, and fetches one level on expand", async () => {
    const user = userEvent.setup();
    const { svc, listTree } = lazyService({
      "/node_modules": {
        items: [{ path: "/node_modules/.package-lock.json", size: 1 }],
        dirs: ["/node_modules/lodash"],
        unwalked: ["/node_modules/lodash"],
        truncated: false,
      },
      "/node_modules/lodash": {
        items: [{ path: "/node_modules/lodash/index.js", size: 1 }],
        dirs: [],
        unwalked: [],
        truncated: false,
      },
    });
    renderTree(svc, ["/node_modules"]);

    // Walked folders keep today's default (open); the pruned one starts closed.
    expect(screen.getByText("a.py")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "node_modules", expanded: false })).toBeInTheDocument();
    expect(listTree).not.toHaveBeenCalled();

    await user.click(screen.getByText("node_modules"));
    await waitFor(() => expect(screen.getByText(".package-lock.json")).toBeInTheDocument());
    expect(listTree).toHaveBeenCalledTimes(1);
    expect(listTree).toHaveBeenCalledWith({ prefix: "/node_modules", depth: 1 });

    // The level's own subfolder is lazy again: closed, nothing fetched for it yet.
    expect(screen.getByRole("button", { name: "lodash", expanded: false })).toBeInTheDocument();
    expect(screen.queryByText("index.js")).not.toBeInTheDocument();
    await user.click(screen.getByText("lodash"));
    await waitFor(() => expect(screen.getByText("index.js")).toBeInTheDocument());
    expect(listTree).toHaveBeenCalledTimes(2);
    expect(listTree).toHaveBeenLastCalledWith({ prefix: "/node_modules/lodash", depth: 1 });

    // Collapsing drops the folder's observer; re-opening shows the cached level
    // AT ONCE (no wait) and refreshes it behind — a folder the agent wrote into
    // while it was closed is right again on its next expand, without anyone
    // having to refetch collapsed folders on every turn.
    await user.click(screen.getByText("node_modules"));
    expect(screen.queryByText(".package-lock.json")).not.toBeInTheDocument();
    await user.click(screen.getByText("node_modules"));
    expect(screen.getByText(".package-lock.json")).toBeInTheDocument();
    // Both levels came back on screen, so both refresh — the nested one was
    // not refetched while its parent was closed (nothing to show it in).
    await waitFor(() => expect(listTree).toHaveBeenCalledTimes(4));
    const refreshed = listTree.mock.calls.slice(2).map((c) => c[0]?.prefix);
    expect(refreshed.sort()).toEqual(["/node_modules", "/node_modules/lodash"]);
  });

  it("keeps honouring a collapse the user persisted before folders could be lazy", () => {
    // The stored set means "toggled away from the default": for a walked folder
    // that is still "collapsed", byte for byte what it meant before.
    localStorage.setItem("rca:tree-collapsed:inv-lazy", JSON.stringify(["/src"]));
    const { svc } = lazyService({});
    renderTree(svc, ["/node_modules"]);
    expect(screen.queryByText("a.py")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "src", expanded: false })).toBeInTheDocument();
  });

  it("says the listing is partial only when the entry budget cut it, not for pruned folders", () => {
    const { svc } = lazyService({});
    const { unmount } = renderTree(svc, ["/node_modules"], false);
    // Derived folders are always lazy; that is not "partial", every IDE does it silently.
    expect(screen.queryByTestId("tree-partial")).not.toBeInTheDocument();
    unmount();

    renderTree(svc, ["/node_modules", "/data"], true);
    expect(screen.getByTestId("tree-partial")).toHaveTextContent(/展開|expanded/);
  });

  it("still asks before a new file would replace one inside a loaded lazy folder", async () => {
    // The replace prompt used to read the preload listing; a file under `dist/`
    // is never in it, so "New file… index.html" would have silently emptied
    // the built page. Every "is it there?" question reads the merged tree.
    const user = userEvent.setup();
    const { svc, listTree } = lazyService({
      "/dist": {
        items: [{ path: "/dist/index.html", size: 1 }],
        dirs: [],
        unwalked: [],
        truncated: false,
      },
    });
    const writeFile = vi.fn(async () => {});
    renderWithQuery(
      <FileServiceProvider value={{ ...svc, writeFile }}>
        <FileTree
          files={[{ path: "/src/a.py", size: 1 }]}
          dirs={["/src", "/dist"]}
          unwalked={["/dist"]}
          activePath={null}
          onOpen={vi.fn()}
        />
      </FileServiceProvider>,
    );
    await user.click(screen.getByText("dist"));
    await waitFor(() => expect(screen.getByText("index.html")).toBeInTheDocument());
    expect(listTree).toHaveBeenCalledTimes(1);

    await user.click(screen.getByText("dist")); // select the folder → New file lands in it
    await user.click(screen.getByText("dist"));
    await user.click(screen.getByTitle("New file in dist/"));
    await user.type(await screen.findByPlaceholderText("file name"), "index.html{Enter}");
    expect(await screen.findByText(/already exists/i)).toBeInTheDocument();
    expect(writeFile).not.toHaveBeenCalled();
  });

  it("does not open a collapsed-before-lazy folder just because it is in the old collapsed set", () => {
    // Users who had collapsed `node_modules/` by hand (the ones who waited 50 s)
    // must not find it auto-expanded — and fetched — after the deploy.
    localStorage.setItem("rca:tree-collapsed:inv-lazy", JSON.stringify(["/node_modules"]));
    const { svc, listTree } = lazyService({});
    renderTree(svc, ["/node_modules"]);
    expect(screen.getByRole("button", { name: "node_modules" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(listTree).not.toHaveBeenCalled();
  });

  it("treats a nested lazy folder as a folder, not a file, for Enter", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    const { svc } = lazyService({
      "/dist": {
        items: [],
        dirs: ["/dist/assets"],
        unwalked: ["/dist/assets"],
        truncated: false,
      },
    });
    renderWithQuery(
      <FileServiceProvider value={svc}>
        <FileTree
          files={[{ path: "/src/a.py", size: 1 }]}
          dirs={["/src", "/dist"]}
          unwalked={["/dist"]}
          activePath={null}
          onOpen={onOpen}
        />
      </FileServiceProvider>,
    );
    await user.click(screen.getByText("dist"));
    await waitFor(() => expect(screen.getByText("assets")).toBeInTheDocument());
    await user.click(screen.getByText("assets")); // selects (and toggles) the nested lazy folder
    await user.keyboard("{Enter}");
    expect(onOpen).not.toHaveBeenCalled();
  });

  // A lazy folder the user has NOT opened has no level in the listing at all,
  // so "is that file already there?" cannot be answered from the tree. It is
  // answered by asking the server — one path, one question (`svc.exists`).
  it("asks the server before an upload would overwrite a file in a folder that is not loaded", async () => {
    const { svc, listTree } = lazyService({});
    const exists = vi.fn(async (path: string) => path === "/dist/index.html");
    const writeFile = vi.fn(async () => {});
    const confirm = vi.fn(() => false);
    vi.stubGlobal("confirm", confirm);
    renderWithQuery(
      <FileServiceProvider value={{ ...svc, exists, writeFile }}>
        <FileTree
          files={[{ path: "/src/a.py", size: 1 }]}
          dirs={["/src", "/dist"]}
          unwalked={["/dist"]}
          activePath={null}
          onOpen={vi.fn()}
        />
      </FileServiceProvider>,
    );
    const drop = {
      dataTransfer: {
        types: ["Files"],
        files: [new File(["x"], "index.html")],
        items: [],
        getData: () => "",
      },
    };
    fireEvent.drop(screen.getByText("dist"), drop);
    await waitFor(() => expect(exists).toHaveBeenCalledWith("/dist/index.html"));
    expect(confirm).toHaveBeenCalled();
    expect(writeFile).not.toHaveBeenCalled();
    expect(listTree).not.toHaveBeenCalled(); // the level need not load to answer
    vi.unstubAllGlobals();
  });

  it("asks the server before a new file would overwrite one in a level that has not arrived yet", async () => {
    const user = userEvent.setup();
    const listTree = vi.fn(() => new Promise<never>(() => {})); // the level never arrives
    const svc: FileService = { ...investigationFileService("rca", "inv-lazy"), listTree };
    const exists = vi.fn(async (path: string) => path === "/dist/index.html");
    const writeFile = vi.fn(async () => {});
    renderWithQuery(
      <FileServiceProvider value={{ ...svc, exists, writeFile }}>
        <FileTree
          files={[{ path: "/src/a.py", size: 1 }]}
          dirs={["/src", "/dist"]}
          unwalked={["/dist"]}
          activePath={null}
          onOpen={vi.fn()}
        />
      </FileServiceProvider>,
    );
    await user.click(screen.getByText("dist")); // opens it; the level is pending
    await user.click(screen.getByTitle("New file in dist/"));
    await user.type(await screen.findByPlaceholderText("file name"), "index.html{Enter}");
    expect(await screen.findByText(/already exists/i)).toBeInTheDocument();
    expect(writeFile).not.toHaveBeenCalled();
  });

  it("opens a collapsed lazy folder and shows the creator when its context menu says New file", async () => {
    const user = userEvent.setup();
    const { svc, listTree } = lazyService({
      "/dist": { items: [], dirs: [], unwalked: [], truncated: false },
    });
    renderWithQuery(
      <FileServiceProvider value={svc}>
        <FileTree
          files={[{ path: "/src/a.py", size: 1 }]}
          dirs={["/src", "/dist"]}
          unwalked={["/dist"]}
          activePath={null}
          onOpen={vi.fn()}
        />
      </FileServiceProvider>,
    );
    fireEvent.contextMenu(screen.getByText("dist"));
    await user.click(await screen.findByRole("button", { name: "New file…" }));
    expect(await screen.findByPlaceholderText("file name")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "dist" })).toHaveAttribute("aria-expanded", "true");
    await waitFor(() => expect(listTree).toHaveBeenCalledTimes(1));
  });

  it("shows that an opened lazy folder is still loading, so empty and pending look different", async () => {
    const user = userEvent.setup();
    const listTree = vi.fn(() => new Promise<never>(() => {}));
    const svc: FileService = { ...investigationFileService("rca", "inv-lazy"), listTree };
    renderTree(svc, ["/node_modules"]);
    await user.click(screen.getByText("node_modules"));
    expect(await screen.findByTestId("lazy-loading")).toBeInTheDocument();
  });
});
