// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { KbDocument } from "./kb";

// An in-memory specstar backend the mocked apiFetch serves: GET envelope,
// GET content blob, POST /blobs/upload, CAS PATCH /source-doc/{id}.
const backend = vi.hoisted(() => ({
  revision: 1,
  blobs: new Map<string, string>(), // file_id → UTF-8 body (test content is text)
  patches: [] as unknown[],
  failPatches: 0, // return 412 this many times before succeeding (CAS retry)
}));

vi.mock("./http", () => ({
  API_BASE: "",
  apiFetch: vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const json = (obj: unknown) =>
      new Response(JSON.stringify(obj), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    const blobMatch = path.match(/^\/source-doc\/[^/]+\/blobs\/(.+)$/);
    if (method === "GET" && blobMatch) {
      return new Response(backend.blobs.get(decodeURIComponent(blobMatch[1])) ?? "", {
        status: 200,
      });
    }
    if (method === "GET" && /^\/source-doc\/[^/]+$/.test(path)) {
      return json({
        data: { content: { file_id: "fid-current", content_type: "text/markdown", size: 3 } },
        revision_info: { revision_id: `rev-${backend.revision}` },
      });
    }
    if (method === "POST" && path === "/blobs/upload") {
      backend.blobs.set("fid-new", "# edited");
      return json({ file_id: "fid-new", size: 8, content_type: "text/markdown" });
    }
    if (method === "PATCH" && /^\/source-doc\/[^/]+$/.test(path)) {
      if (backend.failPatches > 0) {
        backend.failPatches -= 1;
        return new Response("stale", { status: 412 });
      }
      backend.patches.push(JSON.parse(init!.body as string));
      backend.revision += 1;
      return json({ revision_id: `rev-${backend.revision}` });
    }
    return new Response("not found", { status: 404 });
  }),
}));

import { kbFileService } from "./kbFileService";

const docs: KbDocument[] = [
  {
    resource_id: "doc-1",
    path: "/notes.md",
    content_type: "text/markdown",
    created_by: "me",
    status: "ready",
    size: 3,
  },
  {
    resource_id: "img-1",
    path: "/guides/diagram.png",
    content_type: "image/png",
    file_id: "imgfid",
    created_by: "me",
    status: "ready",
  },
];

function makeKb() {
  return {
    deleteDocument: vi.fn(async (_documentId: string) => {}),
    uploadDocument: vi.fn(async (_collectionId: string, _file: File, _path?: string) => ["doc-new"]),
    moveDocument: vi.fn(async (_documentId: string, _to: string) => {}),
  };
}

describe("kbFileService", () => {
  beforeEach(() => {
    backend.revision = 1;
    backend.blobs.clear();
    backend.patches.length = 0;
    backend.failPatches = 0;
  });

  it("scopes to the collection (kb:<id>)", () => {
    const svc = kbFileService("col-1", docs, makeKb());
    expect(svc.scopeId).toBe("kb:col-1");
  });

  it("listFiles maps the document list to FileInfo (flat, no dirs)", async () => {
    const svc = kbFileService("col-1", docs, makeKb());
    expect(await svc.listFiles()).toEqual([
      { path: "/notes.md", size: 3 },
      { path: "/guides/diagram.png", size: 0 },
    ]);
    expect(await svc.listDirs()).toEqual([]);
  });

  it("canonicalises stored paths to a leading slash and resolves ops by that form", async () => {
    // Real uploads store relative paths (no leading slash); the tree's native
    // form is leading-slash. listFiles must present the canonical form, and ops
    // (delete/move/read) must resolve a leading-slash tree path back to the doc.
    const relDocs: KbDocument[] = [
      {
        resource_id: "doc-rel",
        path: "mydir/report.md",
        content_type: "text/markdown",
        created_by: "me",
        status: "ready",
        size: 3,
      },
    ];
    const kb = makeKb();
    const svc = kbFileService("col-1", relDocs, kb, vi.fn());
    expect(await svc.listFiles()).toEqual([{ path: "/mydir/report.md", size: 3 }]);
    await svc.deleteFile("/mydir/report.md"); // resolves despite the stored form
    expect(kb.deleteDocument).toHaveBeenCalledWith("doc-rel");
  });

  it("readFile fetches the RAW content blob (not a projection) and decodes it", async () => {
    backend.blobs.set("fid-current", "# hello\nworld");
    const svc = kbFileService("col-1", docs, makeKb());
    const content = await svc.readFile("/notes.md");
    expect(content).toMatchObject({ kind: "text", path: "/notes.md", text: "# hello\nworld" });
  });

  it("readFile throws for a path with no document", async () => {
    const svc = kbFileService("col-1", docs, makeKb());
    await expect(svc.readFile("/nope.md")).rejects.toThrow(/unknown KB document/);
  });

  it("writeFile saves an existing doc by re-ingesting it — NO If-Match PATCH", async () => {
    // The revision id contains '∕' (U+2215), which can't ride an HTTP header, so
    // a CAS PATCH could never land. Saving re-ingests via the path-keyed upload
    // route instead (overwrite in place + reindex), with no PATCH at all.
    const kb = makeKb();
    const onChanged = vi.fn();
    const svc = kbFileService("col-1", docs, kb, onChanged);
    await svc.writeFile("/notes.md", "# edited");
    expect(kb.uploadDocument).toHaveBeenCalledWith("col-1", expect.any(File), "/notes.md");
    const [, file] = kb.uploadDocument.mock.calls[0]!;
    expect(await file.text()).toBe("# edited");
    expect(backend.patches).toHaveLength(0); // never PATCHes (no fragile CAS header)
    expect(onChanged).toHaveBeenCalled();
  });

  it("writeFile to a new path uploads a fresh document", async () => {
    const kb = makeKb();
    const onChanged = vi.fn();
    const svc = kbFileService("col-1", docs, kb, onChanged);
    const file = new File(["new body"], "added.md", { type: "text/markdown" });
    await svc.writeFile("/added.md", file);
    expect(kb.uploadDocument).toHaveBeenCalledWith("col-1", file, "/added.md");
    expect(backend.patches).toHaveLength(0);
    expect(onChanged).toHaveBeenCalled();
  });

  it("deleteFile delegates to the cascade-aware KB delete", async () => {
    const kb = makeKb();
    const onChanged = vi.fn();
    const svc = kbFileService("col-1", docs, kb, onChanged);
    await svc.deleteFile("/notes.md");
    expect(kb.deleteDocument).toHaveBeenCalledWith("doc-1");
    expect(onChanged).toHaveBeenCalled();
  });

  it("advertises the full op set (create/move/copy/folders all on)", () => {
    const svc = kbFileService("col-1", docs, makeKb());
    expect(svc.caps).toEqual({
      write: true,
      create: true,
      upload: true,
      delete: true,
      move: true,
      copy: true,
      folders: true,
    });
  });

  it("moveFile re-keys the doc through the move route", async () => {
    const kb = makeKb();
    const onChanged = vi.fn();
    const svc = kbFileService("col-1", docs, kb, onChanged);
    await svc.moveFile("/notes.md", "/renamed.md");
    expect(kb.moveDocument).toHaveBeenCalledWith("doc-1", "/renamed.md");
    expect(onChanged).toHaveBeenCalled();
  });

  const folderDocs: KbDocument[] = [
    { resource_id: "d-a", path: "src/a.md", content_type: "text/markdown", created_by: "me", status: "ready", size: 1 },
    { resource_id: "d-b", path: "src/sub/b.md", content_type: "text/markdown", created_by: "me", status: "ready", size: 1 },
    { resource_id: "d-keep", path: "src/.gitkeep", content_type: "text/plain", created_by: "me", status: "ready", size: 1 },
  ];

  it("moveFile on a folder fans out over every descendant doc (incl .gitkeep)", async () => {
    const kb = makeKb();
    const svc = kbFileService("col-1", folderDocs, kb, vi.fn());
    await svc.moveFile("/src", "/dst/src");
    const calls = kb.moveDocument.mock.calls;
    expect(calls).toHaveLength(3);
    expect(calls).toContainEqual(["d-a", "/dst/src/a.md"]);
    expect(calls).toContainEqual(["d-b", "/dst/src/sub/b.md"]);
    expect(calls).toContainEqual(["d-keep", "/dst/src/.gitkeep"]);
  });

  it("copyFile on a folder fans out, copying each descendant under the new path", async () => {
    backend.blobs.set("fid-current", "x");
    const kb = makeKb();
    const svc = kbFileService("col-1", folderDocs, kb, vi.fn());
    await svc.copyFile("/src", "/dst/src");
    const paths = kb.uploadDocument.mock.calls.map((c) => c[2]);
    expect(paths).toHaveLength(3);
    expect(paths).toContain("/dst/src/a.md");
    expect(paths).toContain("/dst/src/sub/b.md");
    expect(paths).toContain("/dst/src/.gitkeep");
  });

  it("deleteFile on a folder fans out, removing every descendant doc", async () => {
    const kb = makeKb();
    const svc = kbFileService("col-1", folderDocs, kb, vi.fn());
    await svc.deleteFile("/src");
    const ids = kb.deleteDocument.mock.calls.map((c) => c[0]);
    expect(ids).toHaveLength(3);
    expect(ids).toEqual(expect.arrayContaining(["d-a", "d-b", "d-keep"]));
  });

  it("copyFile reads the source bytes and uploads them at the new path", async () => {
    backend.blobs.set("fid-current", "# original");
    const kb = makeKb();
    const svc = kbFileService("col-1", docs, kb, vi.fn());
    await svc.copyFile("/notes.md", "/copy.md");
    expect(kb.uploadDocument).toHaveBeenCalled();
    const [col, file, path] = kb.uploadDocument.mock.calls[0]!;
    expect(col).toBe("col-1");
    expect(path).toBe("/copy.md");
    expect(await file.text()).toBe("# original");
  });

  it("mkdir persists a hidden .gitkeep placeholder for the folder", async () => {
    const kb = makeKb();
    const svc = kbFileService("col-1", docs, kb, vi.fn());
    await svc.mkdir("/newfolder");
    const [, , path] = kb.uploadDocument.mock.calls[0]!;
    expect(path).toBe("/newfolder/.gitkeep");
  });

  describe("fileUrl (markdown ref resolution)", () => {
    const svc = kbFileService("col-1", docs, makeKb());

    it("resolves a doc-relative ref to the sibling doc's content blob", () => {
      // a ref in /guides/setup.md → ./diagram.png is the sibling /guides/diagram.png
      expect(svc.fileUrl("./diagram.png", "/guides/setup.md")).toBe(
        "/source-doc/img-1/blobs/imgfid",
      );
    });

    it("resolves a collection-root (absolute) ref", () => {
      expect(svc.fileUrl("/guides/diagram.png", "/notes.md")).toBe(
        "/source-doc/img-1/blobs/imgfid",
      );
    });

    it("passes external URLs / fragments through unchanged", () => {
      expect(svc.fileUrl("https://cdn/x.png", "/notes.md")).toBe("https://cdn/x.png");
      expect(svc.fileUrl("#anchor", "/notes.md")).toBe("#anchor");
      expect(svc.fileUrl(undefined, "/notes.md")).toBe("");
    });

    it("leaves an unknown sibling ref as-is (broken-image marker, not a wrong URL)", () => {
      expect(svc.fileUrl("./missing.png", "/guides/setup.md")).toBe("./missing.png");
    });
  });
});
