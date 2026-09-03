// @vitest-environment happy-dom
import { describe, expect, it, vi } from "vitest";

import type { FileCaps, FileService } from "../../api/fileService";
import type { FileContent, FileInfo } from "../../api/types";
import { dispatchWuiRequest, type BridgeContext } from "./bridge";
import { WUI_PROTOCOL, type WuiRequest } from "./protocol";

const text = (path: string, body: string): FileContent => ({
  kind: "text",
  path,
  size: body.length,
  text: body,
  encoding: "utf-8",
});

function ctx(over: Partial<BridgeContext> = {}, files: Record<string, string> = {}): BridgeContext {
  const fs = {
    scopeId: "item1",
    caps: { write: true, delete: true } as FileCaps,
    listFiles: vi.fn(
      async (): Promise<FileInfo[]> =>
        Object.entries(files).map(([path, body]) => ({ path, size: body.length, read_only: false })),
    ),
    readFile: vi.fn(async (path: string) => {
      if (!(path in files)) throw new Error(`not found: ${path}`);
      return text(path, files[path]);
    }),
    writeFile: vi.fn(async () => {}),
    deleteFile: vi.fn(async () => {}),
    fileDownloadUrl: (path: string) => `/api/files${path}`,
  } as unknown as FileService;
  return { fs, folder: "/sales", openFile: null, me: "alice", ...over };
}

const req = (verb: string, args: Record<string, unknown> = {}): WuiRequest => ({
  proto: WUI_PROTOCOL,
  id: "1",
  verb,
  args,
});

describe("dispatchWuiRequest", () => {
  it("reads any file in the item", async () => {
    const res = await dispatchWuiRequest(req("readFile", { path: "/notes.md" }), ctx({}, { "/notes.md": "hello" }));

    expect(res).toMatchObject({ ok: true, value: { kind: "text", text: "hello" } });
  });

  it("reads a bare path as one next to the page", async () => {
    const c = ctx({}, { "/sales/data.json": "[]" });
    const res = await dispatchWuiRequest(req("readFile", { path: "data.json" }), c);

    expect(res).toMatchObject({ ok: true });
    expect(c.fs.readFile).toHaveBeenCalledWith("/sales/data.json");
  });

  it("writes inside its own folder", async () => {
    const c = ctx();
    const res = await dispatchWuiRequest(req("writeFile", { path: "data.json", text: "[1]" }), c);

    expect(res).toMatchObject({ ok: true });
    expect(c.fs.writeFile).toHaveBeenCalledWith("/sales/data.json", "[1]");
  });

  it("refuses to write outside its folder, and says why in a sentence", async () => {
    // The refusal text ends up in front of someone who cannot open a console,
    // and gets forwarded to the agent verbatim — so `false` is not an answer.
    const c = ctx();
    const res = await dispatchWuiRequest(req("writeFile", { path: "/notes.md", text: "x" }), c);

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toMatch(/only write.*own folder/i);
    expect(res.ok === false && res.error).toContain("/notes.md");
    expect(c.fs.writeFile).not.toHaveBeenCalled();
  });

  it("refuses to delete outside its folder", async () => {
    const c = ctx();
    const res = await dispatchWuiRequest(req("deleteFile", { path: "../notes.md" }), c);

    expect(res).toMatchObject({ ok: false });
    expect(c.fs.deleteFile).not.toHaveBeenCalled();
  });

  it("deletes inside its folder, because writing already allows destroying", async () => {
    const c = ctx();
    const res = await dispatchWuiRequest(req("deleteFile", { path: "old.json" }), c);

    expect(res).toMatchObject({ ok: true });
    expect(c.fs.deleteFile).toHaveBeenCalledWith("/sales/old.json");
  });

  it("refuses a write when the service itself cannot write", async () => {
    const c = ctx({ fs: { ...ctx().fs, caps: { write: false, delete: false } as FileCaps } });
    const res = await dispatchWuiRequest(req("writeFile", { path: "a.json", text: "x" }), c);

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toMatch(/read-only/i);
  });

  it("lists the item's files", async () => {
    const res = await dispatchWuiRequest(req("listFiles"), ctx({}, { "/a.md": "x", "/sales/b.json": "y" }));

    expect(res).toMatchObject({ ok: true });
    expect(res.ok === true && (res.value as { files: FileInfo[] }).files).toHaveLength(2);
  });

  it("says who is looking", async () => {
    const res = await dispatchWuiRequest(req("whoami"), ctx());

    expect(res).toMatchObject({ ok: true, value: { user: "alice" } });
  });

  it("asks the workspace to open a file beside the page", async () => {
    const openFile = vi.fn();
    const res = await dispatchWuiRequest(req("openFile", { path: "/issues/5.md" }), ctx({ openFile }));

    expect(res).toMatchObject({ ok: true });
    expect(openFile).toHaveBeenCalledWith("/issues/5.md");
  });

  it("says so plainly when there is no workspace to open a file in", async () => {
    const res = await dispatchWuiRequest(req("openFile", { path: "/issues/5.md" }), ctx({ openFile: null }));

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toMatch(/cannot open/i);
  });

  it("names an unknown verb instead of failing silently", async () => {
    // The bridge's verb set is closed, so a page asking for something else is a
    // mistake worth reading — most likely an agent inventing an API.
    const res = await dispatchWuiRequest(req("callAgent"), ctx());

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toContain("callAgent");
  });

  it("reports a missing file as a refusal, not a thrown render", async () => {
    const res = await dispatchWuiRequest(req("readFile", { path: "/gone.md" }), ctx());

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toContain("/gone.md");
  });

  it("answers on the id it was asked with, so replies cannot be crossed", async () => {
    const res = await dispatchWuiRequest({ ...req("whoami"), id: "abc" }, ctx());

    expect(res.id).toBe("abc");
    expect(res.proto).toBe(WUI_PROTOCOL);
  });
});
