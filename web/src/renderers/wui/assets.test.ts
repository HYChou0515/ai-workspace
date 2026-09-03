// @vitest-environment happy-dom
import { describe, expect, it, vi } from "vitest";

import type { FileService } from "../../api/fileService";
import type { FileContent } from "../../api/types";
import { folderLoader } from "./assets";

/** A FileService stub with only what the loader touches. */
function svc(files: Record<string, FileContent>): FileService {
  return {
    readFile: vi.fn(async (path: string) => {
      const hit = files[path];
      if (!hit) throw new Error(`not found: ${path}`);
      return hit;
    }),
    fileDownloadUrl: (path: string) => `/api/files${path}`,
  } as unknown as FileService;
}

const text = (path: string, body: string): FileContent => ({
  kind: "text",
  path,
  size: body.length,
  text: body,
  encoding: "utf-8",
});

describe("folderLoader", () => {
  it("reads a sibling file relative to the WUI folder", async () => {
    const fs = svc({ "/sales/app.js": text("/sales/app.js", "hi") });

    expect(await folderLoader(fs, "/sales")("app.js")).toEqual({ kind: "text", text: "hi" });
  });

  it("refuses to look outside the folder at all", async () => {
    // Not "returns null after reading" — the read must never be ISSUED, or the
    // scope is a filter on results rather than a boundary.
    const fs = svc({ "/notes.md": text("/notes.md", "secret") });

    expect(await folderLoader(fs, "/sales")("../notes.md")).toBeNull();
    expect(fs.readFile).not.toHaveBeenCalled();
  });

  it("treats a missing file as absent, not as a failure", async () => {
    // The assembler leaves an unresolved ref in place so CSP names it; that only
    // works if a 404 arrives as `null` instead of throwing through the render.
    const fs = svc({});

    expect(await folderLoader(fs, "/sales")("missing.js")).toBeNull();
  });

  it("fetches a binary sibling's bytes and hands back a data URL", async () => {
    const fs = svc({ "/sales/logo.png": { kind: "binary", path: "/sales/logo.png", size: 2 } });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(new Blob([new Uint8Array([1, 2])], { type: "image/png" }))),
    );

    const asset = await folderLoader(fs, "/sales")("logo.png");

    expect(asset?.kind).toBe("binary");
    expect(asset && "dataUrl" in asset && asset.dataUrl).toMatch(/^data:image\/png;base64,/);
    vi.unstubAllGlobals();
  });

  it("treats an unfetchable binary as absent rather than crashing the page", async () => {
    const fs = svc({ "/sales/logo.png": { kind: "binary", path: "/sales/logo.png", size: 2 } });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 500 })));

    expect(await folderLoader(fs, "/sales")("logo.png")).toBeNull();
    vi.unstubAllGlobals();
  });
});
