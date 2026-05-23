import { describe, expect, it, vi } from "vitest";

import { FileBufferStore } from "./fileBuffer";

function makeIO(initial: Record<string, string> = {}) {
  const files = { ...initial };
  return {
    files,
    readFile: vi.fn(async (_id: string, path: string) => {
      if (!(path in files)) throw new Error(`not found: ${path}`);
      return { kind: "text" as const, path, size: files[path]!.length, text: files[path]! };
    }),
    writeFile: vi.fn(async (_id: string, path: string, body: string) => {
      files[path] = body;
    }),
  };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

describe("FileBufferStore", () => {
  it("loads a path's content lazily", async () => {
    const io = makeIO({ "/a.md": "# hello" });
    const s = new FileBufferStore("inv", io);
    expect(s.snapshot("/a.md").status).toBe("loading");
    s.ensureLoaded("/a.md");
    await tick();
    expect(s.snapshot("/a.md").status).toBe("ready");
    expect(s.snapshot("/a.md").text).toBe("# hello");
  });

  it("only fetches once for concurrent ensureLoaded", async () => {
    const io = makeIO({ "/a.md": "x" });
    const s = new FileBufferStore("inv", io);
    s.ensureLoaded("/a.md");
    s.ensureLoaded("/a.md");
    await tick();
    expect(io.readFile).toHaveBeenCalledTimes(1);
  });

  it("setText updates the shared snapshot immediately (live sync)", () => {
    const io = makeIO({ "/a.md": "old" });
    const s = new FileBufferStore("inv", io, 10_000);
    s.ensureLoaded("/a.md");
    s.setText("/a.md", "new text");
    // Two readers of the same path see the same updated entry — that's
    // what makes split-pane editing live.
    expect(s.snapshot("/a.md").text).toBe("new text");
    expect(s.snapshot("/a.md").save).toBe("dirty");
  });

  it("notifies subscribers on change", () => {
    const io = makeIO({ "/a.md": "x" });
    const s = new FileBufferStore("inv", io, 10_000);
    const cb = vi.fn();
    s.subscribe("/a.md", cb);
    s.setText("/a.md", "y");
    expect(cb).toHaveBeenCalled();
  });

  it("debounced autosave writes to the backend", async () => {
    const io = makeIO({ "/a.md": "old" });
    const s = new FileBufferStore("inv", io, 5);
    s.ensureLoaded("/a.md");
    await tick();
    s.setText("/a.md", "edited");
    await new Promise((r) => setTimeout(r, 20));
    expect(io.writeFile).toHaveBeenCalledWith("inv", "/a.md", "edited");
    expect(s.snapshot("/a.md").save).toBe("saved");
  });

  it("flush is a no-op when clean", async () => {
    const io = makeIO({ "/a.md": "x" });
    const s = new FileBufferStore("inv", io, 10_000);
    s.ensureLoaded("/a.md");
    await tick();
    await s.flush("/a.md");
    expect(io.writeFile).not.toHaveBeenCalled();
  });

  it("surfaces read errors", async () => {
    const io = makeIO({});
    const s = new FileBufferStore("inv", io);
    s.ensureLoaded("/missing");
    await tick();
    expect(s.snapshot("/missing").status).toBe("error");
    expect(s.snapshot("/missing").error).toContain("not found");
  });

  it("reload re-fetches latest backend content", async () => {
    const io = makeIO({ "/a.md": "v1" });
    const s = new FileBufferStore("inv", io);
    s.ensureLoaded("/a.md");
    await tick();
    io.files["/a.md"] = "v2";
    s.reload("/a.md");
    await tick();
    expect(s.snapshot("/a.md").text).toBe("v2");
  });
});
