// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EditModeProvider } from "../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../hooks/fileBuffer";
import { PdfRenderer } from "./PdfRenderer";

const PDF_PATH = "/docs/manual.pdf";
// A few non-UTF8 bytes so the codec keeps them as "binary" (latin1) — exactly
// how a real PDF's bytes round-trip through the editor buffer.
const PDF_TEXT = String.fromCharCode(0x25, 0x50, 0x44, 0x46, 0x2d, 0xac, 0xdc); // %PDF- + two high bytes

function storeWith(text: string): FileBufferStore {
  return new FileBufferStore({
    readFile: vi.fn(async () => ({
      kind: "text" as const,
      path: PDF_PATH,
      size: text.length,
      text,
      encoding: "binary" as const,
    })),
    writeFile: vi.fn(async () => {}),
  });
}

async function renderLoaded(store: FileBufferStore) {
  store.ensureLoaded(PDF_PATH);
  await new Promise((r) => setTimeout(r, 0)); // let the async read settle
  return render(
    <EditModeProvider>
      <FileBufferProvider store={store}>
        <PdfRenderer path={PDF_PATH} />
      </FileBufferProvider>
    </EditModeProvider>,
  );
}

describe("PdfRenderer", () => {
  let createSpy: ReturnType<typeof vi.fn>;
  let revokeSpy: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    createSpy = vi.fn(() => "blob:pdf-mock");
    revokeSpy = vi.fn();
    URL.createObjectURL = createSpy as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = revokeSpy as unknown as typeof URL.revokeObjectURL;
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the PDF bytes in an iframe via an application/pdf blob URL", async () => {
    await renderLoaded(storeWith(PDF_TEXT));

    const frame = await screen.findByTitle(PDF_PATH);
    expect(frame.tagName).toBe("IFRAME");
    expect(frame).toHaveAttribute("src", "blob:pdf-mock");

    // The blob is built as application/pdf from the exact buffer bytes — the
    // browser's native PDF viewer renders it (no mojibake text dump). #117
    expect(createSpy).toHaveBeenCalledTimes(1);
    const blob = createSpy.mock.calls[0][0] as Blob;
    expect(blob.type).toBe("application/pdf");
    expect(blob.size).toBe(PDF_TEXT.length);
  });

  it("does NOT show the raw PDF bytes as text", async () => {
    await renderLoaded(storeWith(PDF_TEXT));
    await screen.findByTitle(PDF_PATH);
    // The mojibake the catch-all text editor used to show must be absent.
    expect(screen.queryByText(/%PDF/)).toBeNull();
  });
});
