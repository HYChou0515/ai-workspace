import { describe, expect, it, vi } from "vitest";

import { FileBufferStore } from "./fileBuffer";

function makeIO(initial: Record<string, string> = {}) {
  const files = { ...initial };
  return {
    files,
    readFile: vi.fn(async (path: string) => {
      if (!(path in files)) throw new Error(`not found: ${path}`);
      return {
        kind: "text" as const,
        path,
        size: files[path]!.length,
        text: files[path]!,
        encoding: "utf-8" as const,
      };
    }),
    writeFile: vi.fn(async (path: string, body: string | ArrayBuffer | Blob) => {
      files[path] = typeof body === "string" ? body : "[bytes]";
    }),
  };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

describe("FileBufferStore", () => {
  it("loads a path's content lazily", async () => {
    const io = makeIO({ "/a.md": "# hello" });
    const s = new FileBufferStore(io);
    expect(s.snapshot("/a.md").status).toBe("loading");
    s.ensureLoaded("/a.md");
    await tick();
    expect(s.snapshot("/a.md").status).toBe("ready");
    expect(s.snapshot("/a.md").text).toBe("# hello");
  });

  it("only fetches once for concurrent ensureLoaded", async () => {
    const io = makeIO({ "/a.md": "x" });
    const s = new FileBufferStore(io);
    s.ensureLoaded("/a.md");
    s.ensureLoaded("/a.md");
    await tick();
    expect(io.readFile).toHaveBeenCalledTimes(1);
  });

  it("setText updates the shared snapshot immediately (live sync)", () => {
    const io = makeIO({ "/a.md": "old" });
    const s = new FileBufferStore(io);
    s.ensureLoaded("/a.md");
    s.setText("/a.md", "new text");
    expect(s.snapshot("/a.md").text).toBe("new text");
    expect(s.snapshot("/a.md").save).toBe("dirty");
    expect(s.isDirty("/a.md")).toBe(true);
  });

  it("does NOT autosave — edits stay dirty until an explicit save", async () => {
    const io = makeIO({ "/a.md": "old" });
    const s = new FileBufferStore(io);
    s.ensureLoaded("/a.md");
    await tick();
    s.setText("/a.md", "edited");
    await new Promise((r) => setTimeout(r, 30));
    expect(io.writeFile).not.toHaveBeenCalled();
    expect(s.snapshot("/a.md").save).toBe("dirty");
  });

  it("editing back to the saved content clears dirty", async () => {
    const io = makeIO({ "/a.md": "orig" });
    const s = new FileBufferStore(io);
    s.ensureLoaded("/a.md");
    await tick();
    s.setText("/a.md", "changed");
    expect(s.isDirty("/a.md")).toBe(true);
    s.setText("/a.md", "orig");
    expect(s.isDirty("/a.md")).toBe(false);
    expect(s.snapshot("/a.md").save).toBe("clean");
  });

  it("save() writes the buffer and clears dirty", async () => {
    const io = makeIO({ "/a.md": "old" });
    const s = new FileBufferStore(io);
    s.ensureLoaded("/a.md");
    await tick();
    s.setText("/a.md", "edited");
    await s.save("/a.md");
    expect(io.writeFile).toHaveBeenCalledWith("/a.md", "edited");
    expect(s.isDirty("/a.md")).toBe(false);
    // a subsequent edit back to the just-saved text is clean again
    s.setText("/a.md", "edited");
    expect(s.isDirty("/a.md")).toBe(false);
  });

  it("save() is a no-op when clean", async () => {
    const io = makeIO({ "/a.md": "x" });
    const s = new FileBufferStore(io);
    s.ensureLoaded("/a.md");
    await tick();
    await s.save("/a.md");
    expect(io.writeFile).not.toHaveBeenCalled();
  });

  it("discard() reverts unsaved edits and clears dirty", async () => {
    const io = makeIO({ "/a.md": "orig" });
    const s = new FileBufferStore(io);
    s.ensureLoaded("/a.md");
    await tick();
    s.setText("/a.md", "scratch");
    s.discard("/a.md");
    expect(s.snapshot("/a.md").text).toBe("orig");
    expect(s.isDirty("/a.md")).toBe(false);
  });

  it("dirtyPaths lists every unsaved path", async () => {
    const io = makeIO({ "/a.md": "1", "/b.md": "2" });
    const s = new FileBufferStore(io);
    s.ensureLoaded("/a.md");
    s.ensureLoaded("/b.md");
    await tick();
    s.setText("/a.md", "edited");
    expect(s.dirtyPaths()).toEqual(["/a.md"]);
  });

  it("notifies subscribers on change", () => {
    const io = makeIO({ "/a.md": "x" });
    const s = new FileBufferStore(io);
    const cb = vi.fn();
    s.subscribe("/a.md", cb);
    s.setText("/a.md", "y");
    expect(cb).toHaveBeenCalled();
  });

  it("surfaces read errors", async () => {
    const io = makeIO({});
    const s = new FileBufferStore(io);
    s.ensureLoaded("/missing");
    await tick();
    expect(s.snapshot("/missing").status).toBe("error");
    expect(s.snapshot("/missing").error).toContain("not found");
  });

  it("reload re-fetches latest backend content", async () => {
    const io = makeIO({ "/a.md": "v1" });
    const s = new FileBufferStore(io);
    s.ensureLoaded("/a.md");
    await tick();
    io.files["/a.md"] = "v2";
    s.reload("/a.md");
    await tick();
    expect(s.snapshot("/a.md").text).toBe("v2");
  });
});
