/**
 * The files an agent put in front of the user, rendered in the chat: an image as
 * the image, any other file as a card with one way to open it.
 *
 * Not inside the collapsed tool card ordinary results use — a chart behind a
 * `<details>` is the same failure as a path in prose.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OpenFileProvider, WorkspaceVisibleProvider } from "../hooks/openFile";
import type { ShownFile } from "../renderers/shownFiles";
import { ShownFiles } from "./ShownFiles";

const chart: ShownFile = {
  path: "/out/revenue.png",
  mime: "image/png",
  size: 145066,
  caption: "月營收趨勢",
};
const report: ShownFile = { path: "/out/Q3-report.pdf", mime: "application/pdf", size: 2202010 };

const fileUrl = (p: string) => `/api/files${p}`;

/** `openFile` = a shell is present. `workspaceVisible` = its file pane is on
 * screen; when it is folded away, opening a file there would look like nothing
 * happened, so the card becomes a plain new-tab link instead. */
function renderShown(
  files: ShownFile[],
  opts: { openFile?: (p: string) => void; workspaceVisible?: boolean } = {},
) {
  const ui = <ShownFiles files={files} fileUrl={fileUrl} />;
  if (!opts.openFile) return render(ui);
  return render(
    <OpenFileProvider value={opts.openFile}>
      <WorkspaceVisibleProvider value={opts.workspaceVisible ?? true}>{ui}</WorkspaceVisibleProvider>
    </OpenFileProvider>,
  );
}

afterEach(cleanup);

describe("ShownFiles", () => {
  it("shows an image as the image itself", () => {
    renderShown([chart]);
    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("src", "/api/files/out/revenue.png");
  });

  it("uses the agent's caption as the image's alt text", () => {
    // The caption is also the best description for anyone who cannot see the image.
    renderShown([chart]);
    expect(screen.getByRole("img", { name: "月營收趨勢" })).toBeInTheDocument();
    expect(screen.getByText("月營收趨勢")).toBeInTheDocument();
  });

  it("falls back to the filename when the agent gave no caption", () => {
    renderShown([{ ...chart, caption: undefined }]);
    expect(screen.getByRole("img", { name: "revenue.png" })).toBeInTheDocument();
  });

  it("names the file and its size", () => {
    renderShown([chart]);
    expect(screen.getByText(/revenue\.png/)).toBeInTheDocument();
    expect(screen.getByText(/141\.7 KB/)).toBeInTheDocument();
  });

  it("shows a non-image file as a card, not a broken image", () => {
    renderShown([report]);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText(/Q3-report\.pdf/)).toBeInTheDocument();
    expect(screen.getByText(/2\.1 MB/)).toBeInTheDocument();
  });

  it("opens the file in the workspace when a shell is there to open it in", () => {
    // renderers/registry.ts already has a viewer for these types.
    const openFile = vi.fn();
    renderShown([report], { openFile });
    fireEvent.click(screen.getByRole("button", { name: /Q3-report\.pdf/ }));
    expect(openFile).toHaveBeenCalledWith("/out/Q3-report.pdf");
  });

  it("clicking an image opens it in the workspace too", () => {
    const openFile = vi.fn();
    renderShown([chart], { openFile });
    fireEvent.click(screen.getByRole("img"));
    expect(openFile).toHaveBeenCalledWith("/out/revenue.png");
  });

  it("falls back to a plain link outside a workspace shell", () => {
    // `useOpenFile()` is null outside a WorkspaceShell; the convention is to
    // degrade rather than draw a dead control.
    renderShown([report]);
    const link = screen.getByRole("link", { name: /Q3-report\.pdf/ });
    expect(link).toHaveAttribute("href", "/api/files/out/Q3-report.pdf");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("opens a new tab instead when the workspace pane is folded away", () => {
    // Folding UNMOUNTS the pane, so opening a file there is invisible. Rather
    // than unfold the workspace on the user's behalf, hand them the file.
    const openFile = vi.fn();
    renderShown([report], { openFile, workspaceVisible: false });

    const link = screen.getByRole("link", { name: /Q3-report\.pdf/ });
    expect(link).toHaveAttribute("href", "/api/files/out/Q3-report.pdf");
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(openFile).not.toHaveBeenCalled();
  });

  it("still names the file when there is no way to fetch it at all", () => {
    // Replay / read-only: still say WHICH file, with nothing that leads nowhere.
    render(<ShownFiles files={[report]} />);
    expect(screen.getByText(/Q3-report\.pdf/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders every declared file", () => {
    renderShown([chart, report]);
    expect(screen.getByRole("img")).toBeInTheDocument();
    expect(screen.getByText(/Q3-report\.pdf/)).toBeInTheDocument();
  });

  it("renders nothing when nothing was declared", () => {
    const { container } = renderShown([]);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ShownFiles — thumbnail sizing", () => {
  it("caps the image at a thumbnail rather than showing it full size", () => {
    // Asked for after seeing a 420px-tall chart in a real browser: at full size
    // the picture pushes the words that explain it off screen. The user opens it
    // when they actually want to look.
    renderShown([chart]);
    const img = screen.getByRole("img");
    expect(img.style.maxWidth).toBe("260px");
    expect(img.style.maxHeight).toBe("260px");
  });
});
