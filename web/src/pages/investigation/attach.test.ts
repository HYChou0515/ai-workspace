import { describe, expect, it, vi } from "vitest";

import { attachPrompt, resolveUploadDir, runAttach, uploadPathFor } from "./attach";

function fileWithRelPath(name: string, rel: string, bytes = 1): File {
  const f = new File([new Uint8Array(bytes)], name);
  Object.defineProperty(f, "webkitRelativePath", { value: rel, configurable: true });
  return f;
}

describe("uploadPathFor (#198)", () => {
  it("lands a single file at {uploadDir}/{name}", () => {
    expect(uploadPathFor("uploads", new File(["x"], "a.csv"))).toBe("/uploads/a.csv");
  });

  it("respects the profile's upload_dir, not a hardcoded folder", () => {
    expect(uploadPathFor("dropbox", new File(["x"], "a.csv"))).toBe("/dropbox/a.csv");
  });

  it("preserves a folder pick's relative path (webkitRelativePath)", () => {
    const f = fileWithRelPath("a.csv", "data/sub/a.csv");
    expect(uploadPathFor("uploads", f)).toBe("/uploads/data/sub/a.csv");
  });

  it("collapses duplicate slashes from a trailing-slash upload_dir", () => {
    expect(uploadPathFor("uploads/", new File(["x"], "a.csv"))).toBe("/uploads/a.csv");
  });
});

describe("attachPrompt (#198, grill Q3)", () => {
  it("is empty for no files", () => {
    expect(attachPrompt([])).toBe("");
  });

  it("a single file → just its path", () => {
    expect(attachPrompt(["/uploads/a.csv"])).toContain("/uploads/a.csv");
    expect(attachPrompt(["/uploads/a.csv"])).not.toContain("\n-");
  });

  it("a handful (≤10) → one path per line", () => {
    const out = attachPrompt(["/uploads/a.csv", "/uploads/b.csv"]);
    expect(out).toContain("/uploads/a.csv");
    expect(out).toContain("/uploads/b.csv");
    expect(out.split("\n").filter((l) => l.includes("/uploads/")).length).toBe(2);
  });

  it("many (>10) → a folder + count summary instead of exploding the draft", () => {
    const paths = Array.from({ length: 12 }, (_, i) => `/uploads/foo/f${i}.csv`);
    const out = attachPrompt(paths);
    expect(out).toContain("12");
    expect(out).toContain("/uploads/foo"); // the common folder
    expect(out).not.toContain("f11.csv"); // does NOT list every file
  });
});

describe("resolveUploadDir (#198)", () => {
  const profiles = [
    { name: "default", upload_dir: "uploads" },
    { name: "intake", upload_dir: "dropbox" },
  ];

  it("resolves the active item's profile to its upload_dir", () => {
    expect(resolveUploadDir(profiles, "intake")).toBe("dropbox");
  });

  it("falls back to uploads for an unknown / missing profile", () => {
    expect(resolveUploadDir(profiles, "ghost")).toBe("uploads");
    expect(resolveUploadDir([], "default")).toBe("uploads");
  });
});

describe("runAttach (#198)", () => {
  it("uploads every file to its derived path and returns them", async () => {
    const upload = vi.fn(async () => {});
    const res = await runAttach({
      files: [new File(["x"], "a.csv"), new File(["y"], "b.csv")],
      uploadDir: "uploads",
      upload,
    });
    expect(res.uploaded).toEqual(["/uploads/a.csv", "/uploads/b.csv"]);
    expect(upload).toHaveBeenCalledTimes(2);
    expect(res.tooLarge).toEqual([]);
    expect(res.failed).toEqual([]);
  });

  it("routes a 413 (over the size cap) to tooLarge and keeps going", async () => {
    const upload = vi.fn(async (path: string) => {
      if (path === "/uploads/big.bin") throw Object.assign(new Error("too big"), { status: 413 });
    });
    const res = await runAttach({
      files: [new File(["x"], "big.bin"), new File(["y"], "ok.csv")],
      uploadDir: "uploads",
      upload,
    });
    expect(res.tooLarge).toEqual(["/uploads/big.bin"]);
    expect(res.uploaded).toEqual(["/uploads/ok.csv"]);
  });

  it("routes a non-413 error to failed and keeps going", async () => {
    const upload = vi.fn(async (path: string) => {
      if (path === "/uploads/x.csv") throw Object.assign(new Error("boom"), { status: 500 });
    });
    const res = await runAttach({
      files: [new File(["x"], "x.csv"), new File(["y"], "y.csv")],
      uploadDir: "uploads",
      upload,
    });
    expect(res.failed).toEqual(["/uploads/x.csv"]);
    expect(res.uploaded).toEqual(["/uploads/y.csv"]);
  });

  it("reports aggregate byte progress, ending at 100% with all files done", async () => {
    const seen: { loadedBytes: number; totalBytes: number; doneFiles: number }[] = [];
    const upload = vi.fn(async (_p: string, file: File, onChunk?: (n: number) => void) => {
      onChunk?.(file.size);
    });
    await runAttach({
      files: [new File([new Uint8Array(100)], "a.bin"), new File([new Uint8Array(300)], "b.bin")],
      uploadDir: "uploads",
      upload,
      onProgress: (p) => seen.push({ ...p }),
    });
    const last = seen[seen.length - 1];
    expect(last.totalBytes).toBe(400);
    expect(last.loadedBytes).toBe(400);
    expect(last.doneFiles).toBe(2);
  });
});
