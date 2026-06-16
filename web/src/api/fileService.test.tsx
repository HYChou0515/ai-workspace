// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// Simulate a sub-path deploy (VITE_BASE_PATH=/sub): file URLs must carry the
// base path so they resolve under a path-stripping proxy (#73).
vi.mock("./http", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./http")>()),
  API_BASE: "/sub",
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
      "/sub/investigations/inv1/files/plot.png",
    );
  });

  it("strips a leading slash and keeps real slashes for nested paths (proxy-safe)", () => {
    expect(resolveServiceUrl("investigations/inv1/files", "/step2/abc.png")).toBe(
      "/sub/investigations/inv1/files/step2/abc.png",
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
    });
  });

  it("builds file URLs on the investigation file route (with the deploy base)", () => {
    const svc = investigationFileService("rca", "inv1");
    expect(svc.fileUrl("./plot.png")).toBe("/sub/a/rca/items/inv1/files/plot.png");
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
