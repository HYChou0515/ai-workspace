// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EditModeProvider } from "../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../hooks/fileBuffer";
import { ImageRenderer } from "./ImageRenderer";

const IMG_PATH = "/photos/bridge.png";
// A couple of high bytes so the editor codec keeps them "binary" (byte-exact).
const IMG_TEXT = String.fromCharCode(0x89, 0x50, 0x4e, 0x47, 0xff, 0xd8);

function storeWith(text: string, path: string = IMG_PATH): FileBufferStore {
  return new FileBufferStore({
    readFile: vi.fn(async () => ({
      kind: "text" as const,
      path,
      size: text.length,
      text,
      encoding: "binary" as const,
    })),
    writeFile: vi.fn(async () => {}),
  });
}

async function renderLoaded(store: FileBufferStore, path: string = IMG_PATH) {
  store.ensureLoaded(path);
  await new Promise((r) => setTimeout(r, 0));
  return render(
    <EditModeProvider>
      <FileBufferProvider store={store}>
        <ImageRenderer path={path} />
      </FileBufferProvider>
    </EditModeProvider>,
  );
}

describe("ImageRenderer", () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:img-mock") as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the image from a blob URL inside a pan/zoom transform", async () => {
    await renderLoaded(storeWith(IMG_TEXT));
    const img = (await screen.findByAltText(IMG_PATH)) as HTMLImageElement;
    expect(img.tagName).toBe("IMG");
    expect(img).toHaveAttribute("src", "blob:img-mock");
    // The image is transformed (the pan/zoom seam), not laid out statically.
    expect(img.style.transform).toMatch(/translate\(.*\) scale\(/);
    // Native image drag is disabled so dragging pans instead of ghost-dragging.
    expect(img).toHaveAttribute("draggable", "false");
  });

  it("sizes a viewBox-only SVG from its viewBox so it fills the pane (not the browser's tiny default)", async () => {
    // happy-dom has no real layout, so pin the container to a known size and
    // let the SVG's own viewBox drive the fitted size (#185).
    const clientW = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(400);
    const clientH = vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(400);
    try {
      const SVG_PATH = "/diagrams/flow.svg";
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 500"></svg>`;
      await renderLoaded(storeWith(svg, SVG_PATH), SVG_PATH);
      const img = (await screen.findByAltText(SVG_PATH)) as HTMLImageElement;
      // contain 1000×500 into 400×400 with upscale allowed → 400×200, so the
      // diagram spans the full pane width instead of sitting tiny in the middle.
      expect(img.style.width).toBe("400px");
      expect(img.style.height).toBe("200px");
    } finally {
      clientW.mockRestore();
      clientH.mockRestore();
    }
  });
});
