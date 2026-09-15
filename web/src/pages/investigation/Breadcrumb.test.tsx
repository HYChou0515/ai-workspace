// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileInfo } from "../../api/types";
import { Breadcrumb, LazyFoldersContext } from "./WorkspaceShell";

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

describe("<Breadcrumb /> under a folder the listing did not enter", () => {
  it("says the level is not loaded instead of calling it empty", async () => {
    // The crumb browser is a listing over the preload; a file under
    // `node_modules/` has no siblings in it. "Empty" would be the one place
    // where pruned reads as hidden.
    const user = userEvent.setup();
    render(
      <LazyFoldersContext.Provider value={["/node_modules"]}>
        <Breadcrumb activeTab="/node_modules/lodash/index.js" files={files} onOpen={vi.fn()} />
      </LazyFoldersContext.Provider>,
    );
    await user.click(screen.getByRole("button", { name: "index.js" }));
    expect(await screen.findByText(/not loaded|尚未載入/)).toBeInTheDocument();
    expect(screen.queryByText("Empty")).not.toBeInTheDocument();
  });

  it("lists the lazy folder at its parent's level — pruned is not hidden there either", async () => {
    const user = userEvent.setup();
    render(
      <LazyFoldersContext.Provider value={["/node_modules"]}>
        <Breadcrumb activeTab="/node_modules/lodash/index.js" files={files} onOpen={vi.fn()} />
      </LazyFoldersContext.Provider>,
    );
    await user.click(screen.getByRole("button", { name: "node_modules" })); // root-level browser
    expect(await screen.findByText("data")).toBeInTheDocument(); // positive control: a walked folder
    // The crumb itself plus the browser's entry for the lazy folder.
    const entries = screen.getAllByRole("button", { name: "node_modules" });
    expect(entries).toHaveLength(2);

    // Drilling into the injected entry must land on "not loaded" — the entry
    // is spelled like the browser's own (slash-less), so it does not become
    // `//node_modules` and read as "Empty".
    await user.click(entries[1]!);
    expect(await screen.findByText(/not loaded|尚未載入/)).toBeInTheDocument();
    expect(screen.queryByText("Empty")).not.toBeInTheDocument();
  });

  it("orders a lazy folder among the walked ones by the tree's rule, not the locale's", async () => {
    // The tree draws siblings in natural order (`lib/treeOrder`): `9-notes`
    // before `10-vendor`. The browser merges the lazy entry into the walked
    // ones and re-sorts; a `localeCompare` there puts `10-vendor` first, so
    // the crumb and the tree would disagree on where the same folder sits.
    const user = userEvent.setup();
    render(
      <LazyFoldersContext.Provider value={["/10-vendor"]}>
        <Breadcrumb
          activeTab="/brief.md"
          files={[...files, { path: "/9-notes/a.md", size: 0 }]}
          onOpen={vi.fn()}
        />
      </LazyFoldersContext.Provider>,
    );
    await user.click(screen.getByRole("button", { name: "brief.md" })); // root-level browser
    expect(await screen.findByText("10-vendor")).toBeInTheDocument();
    const names = screen.getAllByRole("button").map((b) => b.textContent);
    expect(names.indexOf("9-notes")).toBeLessThan(names.indexOf("10-vendor"));
    expect(names.indexOf("10-vendor")).toBeLessThan(names.indexOf("data"));
  });

  it("does not list a lazy folder twice when a file under it is in the preload", async () => {
    // Reachable through the M2 union: a legacy row under a folder primary's
    // budget cut → the folder is in `unwalked` AND has a file in `files`.
    const user = userEvent.setup();
    render(
      <LazyFoldersContext.Provider value={["/node_modules"]}>
        <Breadcrumb
          activeTab="/brief.md"
          files={[...files, { path: "/node_modules/legacy.js", size: 1 }]}
          onOpen={vi.fn()}
        />
      </LazyFoldersContext.Provider>,
    );
    await user.click(screen.getByRole("button", { name: "brief.md" })); // root-level browser
    expect(await screen.findByText("data")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "node_modules" })).toHaveLength(1);
  });
});
