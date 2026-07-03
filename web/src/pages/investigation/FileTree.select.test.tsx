// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileInfo } from "../../api/types";
import { FileTree } from "./FileTree";

afterEach(() => {
  cleanup();
  localStorage.clear(); // usePersistentSet keys collapse-state by scopeId
});

const files: FileInfo[] = [
  { path: "/a.md", size: 1 },
  { path: "/dir/b.md", size: 1 },
  { path: "/dir/c.md", size: 1 },
];

/** Render FileTree in the opt-in controlled select mode. Deliberately WITHOUT a
 * <FileServiceProvider> — the picker (P2) has no writable service, so select
 * mode must run on the optional service. */
function renderSelect(selected: Set<string> = new Set(), files_ = files) {
  const onChange = vi.fn();
  render(
    <FileTree
      files={files_}
      dirs={[]}
      activePath={null}
      onOpen={vi.fn()}
      scopeId="pick"
      select={{ selected, onChange }}
    />,
  );
  return { onChange };
}

describe("<FileTree /> select mode (picker)", () => {
  it("renders a checkbox per file without a FileServiceProvider", () => {
    renderSelect();
    expect(screen.getByRole("checkbox", { name: "a.md" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "b.md" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "c.md" })).toBeInTheDocument();
  });

  it("toggles a leaf into the selection on click", async () => {
    const user = userEvent.setup();
    const { onChange } = renderSelect(new Set());
    await user.click(screen.getByRole("checkbox", { name: "a.md" }));
    expect(onChange).toHaveBeenCalledWith(new Set(["/a.md"]));
  });

  it("toggles a selected leaf back out on click", async () => {
    const user = userEvent.setup();
    const { onChange } = renderSelect(new Set(["/a.md"]));
    await user.click(screen.getByRole("checkbox", { name: "a.md" }));
    expect(onChange).toHaveBeenCalledWith(new Set());
  });

  it("shows a folder as a tri-state checkbox — partial selection is indeterminate", () => {
    renderSelect(new Set(["/dir/b.md"]));
    const folder = screen.getByRole("checkbox", { name: "dir" }) as HTMLInputElement;
    expect(folder.indeterminate).toBe(true);
    expect(folder.checked).toBe(false);
  });

  it("checks a folder when all its leaves are selected", () => {
    renderSelect(new Set(["/dir/b.md", "/dir/c.md"]));
    const folder = screen.getByRole("checkbox", { name: "dir" }) as HTMLInputElement;
    expect(folder.checked).toBe(true);
    expect(folder.indeterminate).toBe(false);
  });

  it("clicking a partly-selected folder selects its whole subtree", async () => {
    const user = userEvent.setup();
    const { onChange } = renderSelect(new Set(["/dir/b.md"]));
    await user.click(screen.getByRole("checkbox", { name: "dir" }));
    expect(onChange).toHaveBeenCalledWith(new Set(["/dir/b.md", "/dir/c.md"]));
  });

  it("clicking a fully-selected folder clears its whole subtree", async () => {
    const user = userEvent.setup();
    const { onChange } = renderSelect(new Set(["/dir/b.md", "/dir/c.md"]));
    await user.click(screen.getByRole("checkbox", { name: "dir" }));
    expect(onChange).toHaveBeenCalledWith(new Set());
  });

  it("collapses a folder via its chevron without changing the selection", async () => {
    const user = userEvent.setup();
    const { onChange } = renderSelect(new Set(["/dir/b.md"]));
    expect(screen.getByRole("checkbox", { name: "b.md" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /collapse dir/i }));
    expect(screen.queryByRole("checkbox", { name: "b.md" })).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
