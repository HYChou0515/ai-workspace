// @vitest-environment happy-dom
import { describe, expect, it, vi } from "vitest";

import { wikiFileService } from "./wikiFileService";

function makeKb() {
  return {
    getWikiPage: vi.fn(async (_c: string, path: string) => ({ path, content: `# ${path}\n` })),
    writeWikiPage: vi.fn(async (_c: string, _path: string, _content: string) => {}),
    moveWikiPage: vi.fn(async (_c: string, _from: string, _to: string) => {}),
    deleteWikiPage: vi.fn(async (_c: string, _path: string) => {}),
  };
}

const pages = ["/index.md", "/entities/reflow.md", "/entities/sub/zone.md"];

describe("wikiFileService", () => {
  it("scopes to the collection and advertises every op EXCEPT upload", () => {
    const svc = wikiFileService("c1", pages, makeKb());
    expect(svc.scopeId).toBe("wiki:c1");
    expect(svc.caps).toEqual({
      write: true,
      create: true,
      upload: false, // the wiki is authored, never uploaded into
      delete: true,
      move: true,
      copy: true,
      folders: true,
      download: false, // #247 covers KB docs + workspace, not the wiki
    });
  });

  it("listFiles maps the wiki page paths to FileInfo", async () => {
    const svc = wikiFileService("c1", pages, makeKb());
    expect(await svc.listFiles()).toEqual([
      { path: "/index.md", size: 0 },
      { path: "/entities/reflow.md", size: 0 },
      { path: "/entities/sub/zone.md", size: 0 },
    ]);
  });

  it("readFile fetches a page's markdown", async () => {
    const svc = wikiFileService("c1", pages, makeKb());
    expect(await svc.readFile("/index.md")).toMatchObject({
      kind: "text",
      path: "/index.md",
      text: "# /index.md\n",
    });
  });

  it("writeFile persists the page content", async () => {
    const kb = makeKb();
    const onChanged = vi.fn();
    const svc = wikiFileService("c1", pages, kb, onChanged);
    await svc.writeFile("/index.md", "# edited\n");
    expect(kb.writeWikiPage).toHaveBeenCalledWith("c1", "/index.md", "# edited\n");
    expect(onChanged).toHaveBeenCalled();
  });

  it("moveFile renames a single page", async () => {
    const kb = makeKb();
    const svc = wikiFileService("c1", pages, kb, vi.fn());
    await svc.moveFile("/index.md", "/home.md");
    expect(kb.moveWikiPage).toHaveBeenCalledWith("c1", "/index.md", "/home.md");
  });

  it("moveFile on a folder fans out over its descendant pages", async () => {
    const kb = makeKb();
    const svc = wikiFileService("c1", pages, kb, vi.fn());
    await svc.moveFile("/entities", "/topics");
    const calls = kb.moveWikiPage.mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls).toContainEqual(["c1", "/entities/reflow.md", "/topics/reflow.md"]);
    expect(calls).toContainEqual(["c1", "/entities/sub/zone.md", "/topics/sub/zone.md"]);
  });

  it("deleteFile removes a single page; a folder fans out", async () => {
    const kb = makeKb();
    const svc = wikiFileService("c1", pages, kb, vi.fn());
    await svc.deleteFile("/index.md");
    expect(kb.deleteWikiPage).toHaveBeenCalledWith("c1", "/index.md");
    kb.deleteWikiPage.mockClear();
    await svc.deleteFile("/entities");
    expect(kb.deleteWikiPage.mock.calls).toHaveLength(2);
  });

  it("mkdir persists a hidden .gitkeep so an empty folder shows", async () => {
    const kb = makeKb();
    const svc = wikiFileService("c1", pages, kb, vi.fn());
    await svc.mkdir("/newfolder");
    expect(kb.writeWikiPage).toHaveBeenCalledWith("c1", "/newfolder/.gitkeep", "");
  });
});
