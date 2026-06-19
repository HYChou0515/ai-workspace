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

function storeWith(text: string): FileBufferStore {
  return new FileBufferStore({
    readFile: vi.fn(async () => ({
      kind: "text" as const,
      path: IMG_PATH,
      size: text.length,
      text,
      encoding: "binary" as const,
    })),
    writeFile: vi.fn(async () => {}),
  });
}

async function renderLoaded(store: FileBufferStore) {
  store.ensureLoaded(IMG_PATH);
  await new Promise((r) => setTimeout(r, 0));
  return render(
    <EditModeProvider>
      <FileBufferProvider store={store}>
        <ImageRenderer path={IMG_PATH} />
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
});
