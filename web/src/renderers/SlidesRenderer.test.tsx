/**
 * A slide deck in the file viewer (docs/plan-pptx-preview.md N3, N6, D6): the
 * server converts it to a PDF and the browser's PDF viewer shows it. A big deck
 * is converted only after the person says yes; a failure says why and offers
 * the original.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, type FileService, type SlidePreview } from "../api/fileService";
import { renderWithQuery } from "../test/queryWrapper";
import { hasEditToggle, pickRenderer } from "./registry";
import { SlidesRenderer } from "./SlidesRenderer";

beforeAll(() => {
  URL.createObjectURL = vi.fn(() => "blob:deck");
  URL.revokeObjectURL = vi.fn();
});
afterEach(cleanup);

function draw(answer: (confirm: boolean) => SlidePreview) {
  const slidePreview = vi.fn(async (_path: string, confirm: boolean) => answer(confirm));
  const svc = {
    scopeId: "i1",
    slidePreview,
    fileDownloadUrl: (p: string) => `/api/files${p}`,
  } as unknown as FileService;
  renderWithQuery(
    <FileServiceProvider value={svc}>
      <SlidesRenderer path="/slides/q3.pptx" />
    </FileServiceProvider>,
  );
  return slidePreview;
}

const pdf: SlidePreview = { kind: "pdf", blob: new Blob(["%PDF"], { type: "application/pdf" }) };

describe("SlidesRenderer", () => {
  it("shows the converted deck in the browser's PDF viewer", async () => {
    draw(() => pdf);

    const frame = await screen.findByTitle("slides/q3.pptx");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame).toHaveAttribute("src", "blob:deck");
  });

  it("asks before converting a big deck, and converts once told yes", async () => {
    const ask = draw((confirm) =>
      confirm ? pdf : { kind: "confirm", size: 42 * 1024 * 1024, limit: 20 * 1024 * 1024 },
    );

    expect(await screen.findByText(/42 MB/)).toBeInTheDocument();
    expect(screen.queryByTitle("slides/q3.pptx")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /預覽|Preview/ }));

    expect(await screen.findByTitle("slides/q3.pptx")).toBeInTheDocument();
    expect(ask).toHaveBeenLastCalledWith("/slides/q3.pptx", true);
  });

  it("says why a deck could not be converted, and offers the original", async () => {
    draw(() => ({ kind: "failed", why: "source file could not be loaded" }));

    expect(await screen.findByText(/source file could not be loaded/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /下載|Download/ })).toHaveAttribute(
      "href",
      "/api/files/slides/q3.pptx",
    );
  });
});

describe("the slide renderer in the registry", () => {
  it.each(["/a/q3.pptx", "/a/Q3.PPT", "/a/q3.odp"])("previews %s", (path) => {
    expect(pickRenderer(path)).toBe("slides");
  });

  it("keeps the Edit toggle — editing shows the file as it is today", () => {
    expect(hasEditToggle("slides")).toBe(true);
  });
});
