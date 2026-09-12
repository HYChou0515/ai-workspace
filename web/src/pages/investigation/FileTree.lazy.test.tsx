// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type FileService, FileServiceProvider, investigationFileService } from "../../api/fileService";
import { renderWithQuery } from "../../test/queryWrapper";
import { FileTree } from "./FileTree";

afterEach(cleanup);

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
    await waitFor(() => expect(listTree).toHaveBeenCalledTimes(3));
    expect(listTree).toHaveBeenLastCalledWith({ prefix: "/node_modules", depth: 1 });
  });

  it("keeps honouring a collapse the user persisted before folders could be lazy", () => {
    // The stored set means "toggled away from the default": for a walked folder
    // that is still "collapsed", byte for byte what it meant before.
    localStorage.setItem("rca:tree-collapsed:inv-lazy", JSON.stringify(["/src"]));
    const { svc } = lazyService({});
    renderTree(svc, ["/node_modules"]);
    expect(screen.queryByText("a.py")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "src", expanded: false })).toBeInTheDocument();
    localStorage.removeItem("rca:tree-collapsed:inv-lazy");
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
});
