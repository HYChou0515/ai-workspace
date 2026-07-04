import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { qk } from "../api/queryKeys";
import { FileBufferStore, reactQueryContentCache } from "./fileBuffer";

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

  it("a .readonly/ snapshot ignores edits and never writes (#205)", async () => {
    const io = makeIO({ "/.readonly/context-card.current.md": "before" });
    const s = new FileBufferStore(io);
    s.ensureLoaded("/.readonly/context-card.current.md");
    await tick();
    s.setText("/.readonly/context-card.current.md", "tampered");
    // edit is dropped — the snapshot stays as loaded and never goes dirty
    expect(s.snapshot("/.readonly/context-card.current.md").text).toBe("before");
    expect(s.isDirty("/.readonly/context-card.current.md")).toBe(false);
    await s.save("/.readonly/context-card.current.md");
    expect(io.writeFile).not.toHaveBeenCalled();
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

describe("FileBufferStore backed by a shared react-query content cache", () => {
  it("loads through the shared cache so a second reader of the same path is served without a refetch", async () => {
    const io = makeIO({ "/a.md": "shared" });
    const qc = new QueryClient();
    const cache = reactQueryContentCache(qc, "scope1", io);
    const s = new FileBufferStore(io, cache);
    s.ensureLoaded("/a.md");
    await tick();
    expect(s.snapshot("/a.md").text).toBe("shared");
    // A second consumer reading the same (scope, path) is served from the cache
    // the buffer already filled — one fetch total (the consolidation win).
    const again = await cache.load("/a.md");
    expect(again.kind === "text" && again.text).toBe("shared");
    expect(io.readFile).toHaveBeenCalledTimes(1);
    // The bytes live under the canonical qk.file key, so any qk.file reader shares them.
    expect(qc.getQueryData(qk.file("scope1", "/a.md"))).toMatchObject({ text: "shared" });
  });

  it("save writes through to the content cache so a fresh reader sees the new content", async () => {
    const io = makeIO({ "/a.md": "old" });
    const qc = new QueryClient();
    const cache = reactQueryContentCache(qc, "s", io);
    const s = new FileBufferStore(io, cache);
    s.ensureLoaded("/a.md");
    await tick();
    s.setText("/a.md", "new");
    await s.save("/a.md");
    expect(qc.getQueryData(qk.file("s", "/a.md"))).toMatchObject({ kind: "text", text: "new" });
  });

  it("reload drops the cached entry so it refetches past the Infinity staleTime", async () => {
    const io = makeIO({ "/a.md": "v1" });
    const qc = new QueryClient();
    const cache = reactQueryContentCache(qc, "s", io);
    const s = new FileBufferStore(io, cache);
    s.ensureLoaded("/a.md");
    await tick();
    io.files["/a.md"] = "v2";
    s.reload("/a.md");
    await tick();
    expect(s.snapshot("/a.md").text).toBe("v2");
    expect(io.readFile).toHaveBeenCalledTimes(2);
  });

  it("with NO cache, behaves exactly as before (reads straight through io)", async () => {
    const io = makeIO({ "/a.md": "plain" });
    const s = new FileBufferStore(io);
    s.ensureLoaded("/a.md");
    await tick();
    expect(s.snapshot("/a.md").text).toBe("plain");
    expect(io.readFile).toHaveBeenCalledTimes(1);
  });
});
