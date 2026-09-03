// @vitest-environment happy-dom
import { describe, expect, it, vi } from "vitest";

import { decodeBytes } from "../../api/encoding";
import type { FileService } from "../../api/fileService";
import { HttpError } from "../../api/http";
import type { FileContent } from "../../api/types";
import { buildWuiDoc, readAsset, folderLoader } from "./assets";

/** A service that fails the way the REAL one does: a typed error carrying a
 * status. The old doubles threw a plain `Error`, so the branch that tells
 * "not there" from "not allowed" could not be reached by any test — the double
 * agreed with the gap. */
function failing(err: unknown): FileService {
  return {
    readFile: vi.fn(async () => {
      throw err;
    }),
    fileDownloadUrl: (path: string) => `/api/files${path}`,
  } as unknown as FileService;
}

/**
 * A FileService stub that answers the way the REAL one does.
 *
 * `investigationFileService` binds `api.readFile`, which reads the bytes and
 * `decodeBytes` them — so it returns `kind: "text"` for EVERY file, with
 * `encoding: "binary"` marking the ones that are not UTF-8. It never emits
 * `kind: "binary"`; only the in-memory mock does. A double that returned the
 * other shape agreed with the bug instead of the contract, and every image in
 * every WUI was broken in production while the test said otherwise.
 */
function svc(files: Record<string, Uint8Array | string>): FileService {
  return {
    readFile: vi.fn(async (path: string): Promise<FileContent> => {
      const hit = files[path];
      if (hit === undefined) throw new Error(`not found: ${path}`);
      const bytes = typeof hit === "string" ? new TextEncoder().encode(hit) : hit;
      const { text, encoding } = decodeBytes(bytes);
      return { kind: "text", path, text, size: bytes.length, encoding };
    }),
    fileDownloadUrl: (path: string) => `/api/files${path}`,
  } as unknown as FileService;
}

/** The first bytes of a real PNG — not valid UTF-8, so it decodes as binary. */
const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

describe("folderLoader", () => {
  it("reads a sibling file relative to the WUI folder", async () => {
    const fs = svc({ "/sales/app.js": "hi" });

    expect(await folderLoader(fs, "/sales")("app.js")).toEqual({ kind: "text", text: "hi" });
  });

  it("refuses to look outside the folder at all", async () => {
    // Not "returns null after reading" — the read must never be ISSUED, or the
    // scope is a filter on results rather than a boundary.
    const fs = svc({ "/notes.md": "secret" });

    expect(await folderLoader(fs, "/sales")("../notes.md")).toBeNull();
    expect(fs.readFile).not.toHaveBeenCalled();
  });

  it("treats a missing file as absent, not as a failure", async () => {
    // The assembler leaves an unresolved ref in place so the page can say so;
    // that only works if a 404 arrives as `null` instead of throwing through
    // the render.
    const fs = svc({});

    expect(await folderLoader(fs, "/sales")("missing.js")).toBeNull();
  });

  it("turns a file the service could not decode as text into a data URL", async () => {
    // This is how EVERY image in a WUI arrives: the service hands back latin1
    // text flagged `encoding: "binary"`, and the bytes are recovered from it.
    const fs = svc({ "/sales/logo.png": PNG });

    const asset = await folderLoader(fs, "/sales")("logo.png");

    expect(asset).toEqual({
      kind: "binary",
      dataUrl: `data:image/png;base64,${btoa(String.fromCharCode(...PNG))}`,
    });
  });

  it("names the media type from the extension, so the browser renders it", async () => {
    const fs = svc({ "/sales/clip.mp4": PNG, "/sales/f.woff2": PNG });
    const load = folderLoader(fs, "/sales");

    expect((await load("clip.mp4")) as { dataUrl: string }).toMatchObject({
      dataUrl: expect.stringContaining("data:video/mp4;base64,"),
    });
    expect((await load("f.woff2")) as { dataUrl: string }).toMatchObject({
      dataUrl: expect.stringContaining("data:font/woff2;base64,"),
    });
  });

  it("keeps a KNOWN text extension as text, whatever its encoding", async () => {
    // The regression this pins: keying on `encoding` made a Big5 `app.js` an
    // image. It is not UTF-8 and it is still a script; the extension is what
    // says what a file is for.
    const big5 = new Uint8Array([0xa7, 0x41, 0xa6, 0x6e]); // 你好 in Big5
    const fs = svc({ "/sales/app.js": big5, "/sales/data.csv": big5 });
    const load = folderLoader(fs, "/sales");

    expect((await load("app.js"))?.kind).toBe("text");
    expect((await load("data.csv"))?.kind).toBe("text");
  });

  it("falls back to a generic data URL for an unlisted extension it cannot decode", async () => {
    // `photo.jfif` is what Windows writes when you save a JPEG, and neither
    // list names it. Left as text it stays a bare reference the CSP refuses —
    // a broken image in a WUI. A browser content-sniffs an `<img>`, so a
    // generic type still renders.
    //
    // This pin is the point: the previous round deleted this fallback ALONG
    // WITH the test that held it, which is exactly how a capability goes
    // missing without anything turning red.
    const fs = svc({ "/sales/photo.jfif": PNG, "/sales/thing.bin": PNG });
    const load = folderLoader(fs, "/sales");

    for (const name of ["photo.jfif", "thing.bin"]) {
      const asset = await load(name);
      expect(asset?.kind).toBe("binary");
      expect(asset && "dataUrl" in asset && asset.dataUrl).toContain(
        "data:application/octet-stream;base64,",
      );
    }
  });

  it("keeps an unlisted extension that DID decode as text", async () => {
    // The fallback asks the bytes, not the name: a `.tpl` of ordinary UTF-8 is
    // text, and turning it into a data URL would break a page that reads it.
    const asset = await folderLoader(svc({ "/sales/page.tpl": "hello" }), "/sales")("page.tpl");

    expect(asset).toEqual({ kind: "text", text: "hello" });
  });

  it("turns an SVG into a data URL, because it is text used as a picture", async () => {
    const asset = await folderLoader(svc({ "/sales/icon.svg": "<svg/>" }), "/sales")("icon.svg");

    expect(asset).toEqual({
      kind: "binary",
      dataUrl: `data:image/svg+xml;base64,${btoa("<svg/>")}`,
    });
  });

  it("base64s a large asset without blowing the stack", async () => {
    // `String.fromCharCode(...bytes)` throws past ~125 000 arguments, and the
    // throw escaped through the render — one oversized SVG replaced the whole
    // page with a stack-overflow message.
    const big = "<svg>" + "x".repeat(400_000) + "</svg>";
    const asset = await folderLoader(svc({ "/sales/big.svg": big }), "/sales")("big.svg");

    expect(asset?.kind).toBe("binary");
  });

  it("still handles a service that reports binary directly", async () => {
    // The FileService contract allows `kind: "binary"` and the in-memory mock
    // emits it; that path fetches the raw route for the bytes.
    const fs = {
      readFile: vi.fn(async (path: string): Promise<FileContent> => ({
        kind: "binary",
        path,
        size: 2,
      })),
      fileDownloadUrl: (path: string) => `/api/files${path}`,
    } as unknown as FileService;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(new Blob([PNG], { type: "image/png" }))),
    );

    const asset = await folderLoader(fs, "/sales")("logo.png");

    expect(asset?.kind).toBe("binary");
    vi.unstubAllGlobals();
  });

  it("treats an unfetchable binary as absent rather than crashing the page", async () => {
    const fs = {
      readFile: vi.fn(async (path: string): Promise<FileContent> => ({
        kind: "binary",
        path,
        size: 2,
      })),
      fileDownloadUrl: (path: string) => `/api/files${path}`,
    } as unknown as FileService;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 500 })));

    expect(await folderLoader(fs, "/sales")("logo.png")).toBeNull();
    vi.unstubAllGlobals();
  });

  it("calls a 404 absence and anything else a failure", async () => {
    // Absence is ordinary and is deliberately not reported; a 403 for a
    // read-only viewer is not, and filing it as absence made it silent.
    expect((await readAsset(failing(new HttpError(404, "nope")), "/a.json")).kind).toBe("missing");
    expect((await readAsset(failing(new HttpError(403, "forbidden")), "/a.json")).kind).toBe(
      "failed",
    );
    expect((await readAsset(failing(new HttpError(500, "boom")), "/a.json")).kind).toBe("failed");
  });

  it("calls a dropped connection a failure, not an absence", async () => {
    // `fetch` rejects with a TypeError when it cannot complete at all. This was
    // named in the fix that introduced the three outcomes and not handled by it.
    const read = await readAsset(failing(new TypeError("Failed to fetch")), "/a.json");

    expect(read.kind).toBe("failed");
  });

  it("treats a plain error as absence, which is all the KB service means by one", async () => {
    expect((await readAsset(failing(new Error("unknown KB document")), "/a.json")).kind).toBe(
      "missing",
    );
  });
});

describe("buildWuiDoc", () => {
  it("says the entry could not be READ, rather than that it is not there", async () => {
    // "This WUI has no index.html to open" is a false sentence to show someone
    // who can see index.html in the tree — and it sends them looking for the
    // wrong thing.
    const opening = buildWuiDoc(failing(new HttpError(403, "forbidden")), "/w", "index.html");

    // What matters is what it does NOT say. And the sentence it does say has to
    // be one: `err.message` is "read /w/index.html failed: 403", an internal
    // path and a bare number, shown to someone with no console to open.
    await expect(opening).rejects.toThrow(/permission/i);
    await expect(opening).rejects.not.toThrow(/no index\.html/);
  });

  it("still says it plainly when the entry really is absent", async () => {
    await expect(buildWuiDoc(failing(new HttpError(404, "nope")), "/w", "index.html")).rejects.toThrow(
      /no index\.html/,
    );
  });
});
