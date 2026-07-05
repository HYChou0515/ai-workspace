// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../../api/fileService";
import type { FileInfo } from "../../api/types";
import { DialogProvider } from "../../components/Dialog";
import { FileTree } from "./FileTree";

afterEach(cleanup);

const files: FileInfo[] = [{ path: "/a.md", size: 1 }];

function renderTree() {
  render(
    <FileServiceProvider value={investigationFileService("rca", "inv")}>
      <DialogProvider>
        <FileTree files={files} dirs={[]} activePath={null} onOpen={vi.fn()} />
      </DialogProvider>
    </FileServiceProvider>,
  );
}

describe("FileTree sticky header (#460 P3)", () => {
  it("pins an opaque header that masks rows scrolling beneath it", () => {
    renderTree();
    const header = screen.getByTestId("file-tree-header");
    // Sticky anchor + a raised stacking so no row/badge paints over it.
    expect(header.style.position).toBe("sticky");
    expect(header.style.zIndex).toBe("2");
    // The top spacing lives INSIDE the opaque sticky header (not as transparent
    // scroll-container padding above it), so rows can't peek through a top band.
    expect(header.style.padding).toMatch(/^10px /);
  });
});
