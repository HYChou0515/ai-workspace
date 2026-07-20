import { describe, expect, it, vi } from "vitest";

import { attachPrompt, resolveUploadDir, runAttach, uploadPathFor } from "./attach";

function fileWithRelPath(name: string, rel: string, bytes = 1): File {
  const f = new File([new Uint8Array(bytes)], name);
  Object.defineProperty(f, "webkitRelativePath", { value: rel, configurable: true });
  return f;
}

describe("uploadPathFor (#198)", () => {
  it("lands a single file at {uploadDir}/{name}", () => {
    expect(uploadPathFor("uploads", new File(["x"], "a.csv"))).toBe("uploads/a.csv");
  });

  it("respects the profile's upload_dir, not a hardcoded folder", () => {
    expect(uploadPathFor("dropbox", new File(["x"], "a.csv"))).toBe("dropbox/a.csv");
  });

  it("preserves a folder pick's relative path (webkitRelativePath)", () => {
    const f = fileWithRelPath("a.csv", "data/sub/a.csv");
    expect(uploadPathFor("uploads", f)).toBe("uploads/data/sub/a.csv");
  });

  it("collapses duplicate slashes from a trailing-slash upload_dir", () => {
    expect(uploadPathFor("uploads/", new File(["x"], "a.csv"))).toBe("uploads/a.csv");
  });
});

describe("attachPrompt (#198, grill Q3)", () => {
  it("names the file the way the agent's own tools will", () => {
    // This draft is the FIRST thing the model ever reads about an attached file,
    // and its `list_files` prints `uploads/a.csv`. A `/uploads/a.csv` here taught
    // it a path its own shell resolves against the SYSTEM root.
    expect(attachPrompt(["uploads/a.csv"])).not.toContain("/uploads");
  });

  it("is empty for no files", () => {
    expect(attachPrompt([])).toBe("");
  });

  it("a single file → just its path", () => {
    expect(attachPrompt(["uploads/a.csv"])).toContain("uploads/a.csv");
    expect(attachPrompt(["uploads/a.csv"])).not.toContain("\n-");
  });

  it("a handful (≤10) → one path per line", () => {
    const out = attachPrompt(["uploads/a.csv", "uploads/b.csv"]);
    expect(out).toContain("uploads/a.csv");
    expect(out).toContain("uploads/b.csv");
    expect(out.split("\n").filter((l) => l.includes("uploads/")).length).toBe(2);
  });

  it("many (>10) → a folder + count summary instead of exploding the draft", () => {
    const paths = Array.from({ length: 12 }, (_, i) => `uploads/foo/f${i}.csv`);
    const out = attachPrompt(paths);
    expect(out).toContain("12");
    expect(out).toContain("uploads/foo"); // the common folder
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
    expect(res.uploaded).toEqual(["uploads/a.csv", "uploads/b.csv"]);
    expect(upload).toHaveBeenCalledTimes(2);
    expect(res.tooLarge).toEqual([]);
    expect(res.failed).toEqual([]);
  });

  it("routes a 413 (over the size cap) to tooLarge and keeps going", async () => {
    const upload = vi.fn(async (path: string) => {
      if (path === "uploads/big.bin") throw Object.assign(new Error("too big"), { status: 413 });
    });
    const res = await runAttach({
      files: [new File(["x"], "big.bin"), new File(["y"], "ok.csv")],
      uploadDir: "uploads",
      upload,
    });
    expect(res.tooLarge).toEqual(["uploads/big.bin"]);
    expect(res.uploaded).toEqual(["uploads/ok.csv"]);
  });

  it("routes a 507 (over the workspace quota) to overQuota and keeps going", async () => {
    const upload = vi.fn(async (path: string) => {
      if (path === "uploads/big.bin")
        throw Object.assign(new Error("out of space"), { status: 507 });
    });
    const res = await runAttach({
      files: [new File(["x"], "big.bin"), new File(["y"], "ok.csv")],
      uploadDir: "uploads",
      upload,
    });
    expect(res.overQuota).toEqual(["uploads/big.bin"]);
    expect(res.tooLarge).toEqual([]);
    expect(res.uploaded).toEqual(["uploads/ok.csv"]);
  });

  it("routes a non-413 error to failed and keeps going", async () => {
    const upload = vi.fn(async (path: string) => {
      if (path === "uploads/x.csv") throw Object.assign(new Error("boom"), { status: 500 });
    });
    const res = await runAttach({
      files: [new File(["x"], "x.csv"), new File(["y"], "y.csv")],
      uploadDir: "uploads",
      upload,
    });
    expect(res.failed).toEqual(["uploads/x.csv"]);
    expect(res.uploaded).toEqual(["uploads/y.csv"]);
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

/**
 * "It said the upload failed, but the file was there."
 *
 * `uploadFile` rejects on a network drop (status 0) and on a gateway status —
 * but those arrive AFTER the body has been sent, so the server may well have
 * stored the file already. Declaring failure then is both wrong and expensive:
 * the user is told to retry a file that is on disk, and the path never makes it
 * into their message, so the agent cannot see the file they just attached.
 *
 * An inconclusive result is a question, not an answer: go and look.
 */
describe("runAttach — an inconclusive upload is verified, not assumed failed", () => {
  const file = (name: string) => new File(["x"], name, { type: "text/plain" });

  it("counts a gateway-cut upload as uploaded when the file is actually there", async () => {
    const res = await runAttach({
      files: [file("a.txt")],
      uploadDir: "uploads",
      upload: async () => {
        throw Object.assign(new Error("write failed: 504"), { status: 504 });
      },
      verify: async () => true, // it landed
    });

    expect(res.uploaded).toEqual(["uploads/a.txt"]);
    expect(res.failed).toEqual([]);
  });

  it("still reports a failure when the file really is not there", async () => {
    const res = await runAttach({
      files: [file("b.txt")],
      uploadDir: "uploads",
      upload: async () => {
        throw Object.assign(new Error("write failed: network error"), { status: 0 });
      },
      verify: async () => false,
    });

    expect(res.uploaded).toEqual([]);
    expect(res.failed).toEqual(["uploads/b.txt"]);
  });

  // A definite rejection needs no second opinion — asking would only be slower
  // and could mistake a leftover file for a success.
  it("does not second-guess a definite rejection", async () => {
    const verify = vi.fn(async () => true);
    const res = await runAttach({
      files: [file("c.txt")],
      uploadDir: "uploads",
      upload: async () => {
        throw Object.assign(new Error("write failed: 413"), { status: 413 });
      },
      verify,
    });

    expect(res.tooLarge).toEqual(["uploads/c.txt"]);
    expect(verify).not.toHaveBeenCalled();
  });

  it("falls back to reporting a failure when it cannot check", async () => {
    const res = await runAttach({
      files: [file("d.txt")],
      uploadDir: "uploads",
      upload: async () => {
        throw Object.assign(new Error("write failed: 502"), { status: 502 });
      },
      verify: async () => {
        throw new Error("cannot list files either");
      },
    });

    expect(res.failed).toEqual(["uploads/d.txt"]);
  });
});
