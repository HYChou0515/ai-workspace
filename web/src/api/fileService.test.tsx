// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Simulate a sub-path deploy (VITE_BASE_PATH=/sub): file URLs must carry the
// base path so they resolve under a path-stripping proxy (#73).
vi.mock("./http", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./http")>()),
  API_BASE: "/sub",
  API_PREFIX: "/sub/api",
}));

import { api } from "./index";
import { QueryWrap } from "../test/queryWrapper";
import {
  type FileService,
  FileServiceProvider,
  investigationFileService,
  resolveServiceUrl,
  useFileList,
  useFileService,
} from "./fileService";

describe("resolveServiceUrl (#73)", () => {
  it("prefixes the deploy base path on a workspace-relative reference", () => {
    expect(resolveServiceUrl("investigations/inv1/files", "./plot.png")).toBe(
      "/sub/api/investigations/inv1/files/plot.png",
    );
  });

  it("strips a leading slash and keeps real slashes for nested paths (proxy-safe)", () => {
    expect(resolveServiceUrl("investigations/inv1/files", "/step2/abc.png")).toBe(
      "/sub/api/investigations/inv1/files/step2/abc.png",
    );
  });

  it("passes schemes / protocol-relative / #fragments through unchanged", () => {
    const base = "investigations/inv1/files";
    expect(resolveServiceUrl(base, "data:image/png;base64,AAAA")).toBe("data:image/png;base64,AAAA");
    expect(resolveServiceUrl(base, "https://cdn/x.png")).toBe("https://cdn/x.png");
    expect(resolveServiceUrl(base, "//host/x.png")).toBe("//host/x.png");
    expect(resolveServiceUrl(base, "#section-2")).toBe("#section-2");
    expect(resolveServiceUrl(base, undefined)).toBe("");
  });
});

describe("investigationFileService", () => {
  it("scopes to the investigation id and advertises full capabilities", () => {
    const svc = investigationFileService("rca", "inv1");
    expect(svc.scopeId).toBe("inv1");
    expect(svc.caps).toEqual({
      write: true,
      create: true,
      upload: true,
      delete: true,
      move: true,
      copy: true,
      folders: true,
      download: true,
    });
  });

  it("builds file URLs on the investigation file route (with the deploy base)", () => {
    const svc = investigationFileService("rca", "inv1");
    expect(svc.fileUrl("./plot.png")).toBe("/sub/api/a/rca/items/inv1/files/plot.png");
  });

  // A relative ref means the file NEXT TO the document it was written in —
  // what `![](./a.png)` means in GitHub, in a VSCode preview, and to whoever
  // wrote it. Resolving every ref against the workspace root instead made an
  // image render only when its document happened to sit at the root, and made
  // the file-tree half of the shell disagree with the KB half over one document.
  describe("fileUrl — a relative ref resolves against the document, not the root", () => {
    const FILES = "/sub/api/a/rca/items/inv1/files";
    const svc = () => investigationFileService("rca", "inv1");

    it("resolves an explicit sibling ref to the doc's own folder", () => {
      expect(svc().fileUrl("./plot.png", "/reports/r.md")).toBe(`${FILES}/reports/plot.png`);
    });

    it("resolves a bare sibling ref the same way", () => {
      expect(svc().fileUrl("plot.png", "/reports/r.md")).toBe(`${FILES}/reports/plot.png`);
    });

    // `..` must be resolved HERE. Left in the URL, the browser collapses it
    // against the API route and walks out of the file endpoint altogether
    // (`…/items/inv1/shared/plot.png` — a route that does not exist).
    it("resolves `..` itself instead of leaving it for the browser", () => {
      const url = svc().fileUrl("../shared/plot.png", "/reports/r.md");
      expect(url).toBe(`${FILES}/shared/plot.png`);
      expect(new URL(url, "http://h").pathname).toBe(url);
    });

    it("keeps an absolute ref workspace-root-relative", () => {
      expect(svc().fileUrl("/plot.png", "/reports/r.md")).toBe(`${FILES}/plot.png`);
    });

    it("resolves a ref in a root document against the root", () => {
      expect(svc().fileUrl("./plot.png", "/report.v2.md")).toBe(`${FILES}/plot.png`);
    });

    // Chat markdown has no containing document (`AgentEntryView`), so a ref
    // there can only mean workspace-root-relative — that meaning must survive.
    it("stays root-relative when the caller has no document to resolve against", () => {
      expect(svc().fileUrl("out/sine.png")).toBe(`${FILES}/out/sine.png`);
    });

    it("passes external refs through even with a document to resolve against", () => {
      const s = svc();
      expect(s.fileUrl("https://cdn/x.png", "/reports/r.md")).toBe("https://cdn/x.png");
      expect(s.fileUrl("data:image/png;base64,AAAA", "/reports/r.md")).toBe(
        "data:image/png;base64,AAAA",
      );
      expect(s.fileUrl("#section-2", "/reports/r.md")).toBe("#section-2");
      expect(s.fileUrl(undefined, "/reports/r.md")).toBe("");
    });
  });

  it("builds a single-file download URL on the file route (#247)", () => {
    const svc = investigationFileService("rca", "inv1");
    expect(svc.fileDownloadUrl("/data/a.csv")).toBe("/sub/api/a/rca/items/inv1/files/data/a.csv");
  });

  it("builds a folder download stream URL carrying the prefix (#247)", () => {
    const svc = investigationFileService("rca", "inv1");
    expect(svc.dirDownloadUrl("dl123", "/data")).toBe(
      "/sub/api/a/rca/items/inv1/files/download/dl123?prefix=%2Fdata",
    );
  });

  it("prepares a folder download via POST and returns the handle (#247)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ download_id: "d1", filename: "data.zip", size: 9 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const res = await investigationFileService("rca", "inv1").prepareDirDownload("/data");
    expect(res).toEqual({ download_id: "d1", filename: "data.zip", size: 9 });
    const url = String(fetchSpy.mock.calls[0][0]);
    expect(url).toContain("/a/rca/items/inv1/files/download/prepare?prefix=%2Fdata");
    expect(fetchSpy.mock.calls[0][1]).toMatchObject({ method: "POST" });
    fetchSpy.mockRestore();
  });

  it("rejects a failed folder-download prepare (#247)", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("nope", { status: 404 }));
    await expect(investigationFileService("rca", "inv1").prepareDirDownload("/x")).rejects.toThrow();
    fetchSpy.mockRestore();
  });

  it("delegates each op to the investigation file API with the bound id", async () => {
    const svc = investigationFileService("rca", "inv1");
    const write = vi.spyOn(api, "writeFile").mockResolvedValue(undefined);
    const del = vi.spyOn(api, "deleteFile").mockResolvedValue(undefined);
    const move = vi.spyOn(api, "moveFile").mockResolvedValue(undefined);
    await svc.writeFile("/a.md", "hi");
    await svc.deleteFile("/a.md");
    await svc.moveFile("/a.md", "/b.md");
    expect(write).toHaveBeenCalledWith("rca", "inv1", "/a.md", "hi");
    expect(del).toHaveBeenCalledWith("rca", "inv1", "/a.md");
    expect(move).toHaveBeenCalledWith("rca", "inv1", "/a.md", "/b.md");
  });
});

describe("useFileService", () => {
  it("throws when used without a provider", () => {
    expect(() => renderHook(() => useFileService())).toThrow(/FileServiceProvider/);
  });
});

describe("useFileList", () => {
  function fakeService(over: Partial<FileService> = {}): FileService {
    return {
      ...investigationFileService("rca", "col-1"),
      listFiles: vi.fn(async () => [{ path: "/a.md", size: 1 }]),
      listDirs: vi.fn(async () => ["/sub"]),
      listTree: vi.fn(async () => ({ items: [{ path: "/a.md", size: 1 }], dirs: ["/sub"] })),
      ...over,
    };
  }

  it("merges the service's files + dirs under the scoped cache key", async () => {
    const svc = fakeService();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryWrap>
        <FileServiceProvider value={svc}>{children}</FileServiceProvider>
      </QueryWrap>
    );
    const { result } = renderHook(() => useFileList(), { wrapper });
    await waitFor(() => expect(result.current.kind).toBe("ready"));
    if (result.current.kind !== "ready") throw new Error("not ready");
    expect(result.current.items).toEqual([{ path: "/a.md", size: 1 }]);
    expect(result.current.dirs).toEqual(["/sub"]);
  });
});

/**
 * The rule itself is covered in writeVerified.test.ts; this is about the WIRING.
 * An unwired rule is an unfixed bug, and this is the seam every writer in the
 * app goes through — the file tree, the composer's attachments, the skills /
 * workflows / collections pickers, the editor's save, both KB IDEs.
 */
describe("investigationFileService.writeFile — one definition of success", () => {
  afterEach(() => vi.restoreAllMocks());

  it("succeeds when a cut connection turns out to have stored the file", async () => {
    vi.spyOn(api, "writeFile").mockRejectedValue(
      Object.assign(new Error("network error"), { status: 0 }),
    );
    const list = vi
      .spyOn(api, "listFiles")
      .mockResolvedValue([{ path: "/uploads/a.txt", size: 1 } as never]);

    await expect(
      investigationFileService("rca", "inv").writeFile("/uploads/a.txt", "x"),
    ).resolves.toBeUndefined();
    expect(list).toHaveBeenCalled();
  });

  it("still fails when the write really did not land", async () => {
    vi.spyOn(api, "writeFile").mockRejectedValue(
      Object.assign(new Error("gateway timeout"), { status: 504 }),
    );
    vi.spyOn(api, "listFiles").mockResolvedValue([]);

    await expect(
      investigationFileService("rca", "inv").writeFile("/uploads/a.txt", "x"),
    ).rejects.toMatchObject({ status: 504 });
  });

  it("does not ask the file list about a definite refusal", async () => {
    vi.spyOn(api, "writeFile").mockRejectedValue(
      Object.assign(new Error("too large"), { status: 413 }),
    );
    const list = vi.spyOn(api, "listFiles");

    await expect(
      investigationFileService("rca", "inv").writeFile("/uploads/a.txt", "x"),
    ).rejects.toMatchObject({ status: 413 });
    expect(list).not.toHaveBeenCalled();
  });
});

describe("useFileList — one traversal", () => {
  function fakeService(over: Partial<FileService> = {}): FileService {
    return { ...investigationFileService("rca", "col-1"), ...over };
  }

  it("asks for the tree once instead of listing files and folders separately", async () => {
    // Two hooks share `qk.files(scopeId)`: the shell's listing and this one.
    // While they had DIFFERENT query functions — one fetching a combined tree,
    // this one `Promise.all([listFiles, listDirs])` — opening an item fetched
    // the same workspace twice over, and each half walked the whole tree.
    const listFiles = vi.fn(async () => []);
    const listDirs = vi.fn(async () => []);
    const listTree = vi.fn(async () => ({ items: [], dirs: [] }));
    const svc = fakeService({ listFiles, listDirs, listTree });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryWrap>
        <FileServiceProvider value={svc}>{children}</FileServiceProvider>
      </QueryWrap>
    );

    const { result } = renderHook(() => useFileList(), { wrapper });
    await waitFor(() => expect(result.current.kind).toBe("ready"));

    expect(listTree).toHaveBeenCalledTimes(1);
    expect(listFiles).not.toHaveBeenCalled();
    expect(listDirs).not.toHaveBeenCalled();
  });
});
