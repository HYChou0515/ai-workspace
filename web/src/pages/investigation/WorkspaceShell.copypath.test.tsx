// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TabContextMenu } from "./WorkspaceShell";

afterEach(cleanup);

/** The editor tab's own "Copy path" is the second exit the store's `/a.md` key has
 * to the outside world (the file tree's menu is the first). Both hand the user a
 * string they paste into a chat message or a shell, where a leading slash means
 * the system root — so both copy the workspace-relative form. */
describe("editor tab context menu — copy path", () => {
  function renderMenu(path: string) {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(
      <TabContextMenu
        path={path}
        x={0}
        y={0}
        pinned={false}
        onClose={vi.fn()}
        onCloseTab={vi.fn()}
        onTogglePin={vi.fn()}
        onCloseOthers={vi.fn()}
        onCloseToRight={vi.fn()}
        onCloseAll={vi.fn()}
        onSplit={vi.fn()}
      />,
    );
    return writeText;
  }

  it("copies the path relative to the workspace", async () => {
    const user = userEvent.setup();
    const writeText = renderMenu("/data/x.csv");
    await user.click(screen.getByRole("button", { name: /copy path/i }));
    expect(writeText).toHaveBeenCalledWith("data/x.csv");
  });

  it("copies a root-level file without inventing a leading dot or slash", async () => {
    const user = userEvent.setup();
    const writeText = renderMenu("/brief.md");
    await user.click(screen.getByRole("button", { name: /copy path/i }));
    expect(writeText).toHaveBeenCalledWith("brief.md");
  });
});
