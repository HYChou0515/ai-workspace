import { describe, expect, it } from "vitest";

import { extractClipboardFiles, isImage, nameImageFile, readTransferEntries } from "./transfer";

describe("isImage (#364)", () => {
  it("is true for an image mime", () => {
    expect(isImage(new File(["x"], "a.png", { type: "image/png" }))).toBe(true);
  });

  it("is false for a non-image mime", () => {
    expect(isImage(new File(["x"], "a.csv", { type: "text/csv" }))).toBe(false);
  });

  it("is false when the type is empty", () => {
    expect(isImage(new File(["x"], "a"))).toBe(false);
  });
});

describe("nameImageFile (#364)", () => {
  it("gives a nameless clipboard image a stamped, mime-derived name", () => {
    const f = nameImageFile(new File(["x"], "", { type: "image/png" }), 1234);
    expect(f.name).toBe("pasted-image-1234.png");
    expect(f.type).toBe("image/png");
  });

  it("renames the browser-default image.png", () => {
    expect(nameImageFile(new File(["x"], "image.png", { type: "image/png" }), 7).name).toBe(
      "pasted-image-7.png",
    );
  });

  it("maps image/jpeg to a jpg extension", () => {
    expect(nameImageFile(new File(["x"], "", { type: "image/jpeg" }), 9).name).toBe(
      "pasted-image-9.jpg",
    );
  });

  it("keeps a meaningful filename untouched", () => {
    expect(nameImageFile(new File(["x"], "diagram.png", { type: "image/png" }), 1).name).toBe(
      "diagram.png",
    );
  });
});

function clip(
  items: { kind: string; type: string; file: File | null }[],
  files: File[] = [],
): DataTransfer {
  return {
    items: items.map((i) => ({ kind: i.kind, type: i.type, getAsFile: () => i.file })),
    files,
  } as unknown as DataTransfer;
}

describe("extractClipboardFiles (#364)", () => {
  it("returns empty arrays for a plain-text paste", () => {
    const dt = clip([{ kind: "string", type: "text/plain", file: null }]);
    expect(extractClipboardFiles(dt, 0)).toEqual({ images: [], files: [] });
  });

  it("harvests a pasted image blob as an image, with a stamped name", () => {
    const img = new File(["x"], "image.png", { type: "image/png" });
    const out = extractClipboardFiles(clip([{ kind: "file", type: "image/png", file: img }]), 5);
    expect(out.files).toHaveLength(0);
    expect(out.images.map((f) => f.name)).toEqual(["pasted-image-5.png"]);
  });

  it("classifies a non-image pasted file as a file, keeping its name", () => {
    const doc = new File(["x"], "notes.txt", { type: "text/plain" });
    const out = extractClipboardFiles(clip([{ kind: "file", type: "text/plain", file: doc }]), 0);
    expect(out.images).toHaveLength(0);
    expect(out.files.map((f) => f.name)).toEqual(["notes.txt"]);
  });

  it("falls back to .files when there are no item blobs (OS file copy)", () => {
    const doc = new File(["x"], "a.pdf", { type: "application/pdf" });
    const out = extractClipboardFiles(clip([], [doc]), 0);
    expect(out.files.map((f) => f.name)).toEqual(["a.pdf"]);
  });

  it("gives several pasted images distinct names", () => {
    const a = new File(["x"], "image.png", { type: "image/png" });
    const b = new File(["y"], "image.png", { type: "image/png" });
    const out = extractClipboardFiles(
      clip([
        { kind: "file", type: "image/png", file: a },
        { kind: "file", type: "image/png", file: b },
      ]),
      100,
    );
    expect(new Set(out.images.map((f) => f.name)).size).toBe(2);
  });

  it("is empty when there is nothing on the clipboard", () => {
    expect(extractClipboardFiles(null, 0)).toEqual({ images: [], files: [] });
  });
});

interface FakeEntry {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file?: (cb: (f: File) => void) => void;
  createReader?: () => { readEntries: (cb: (e: FakeEntry[]) => void) => void };
}

function fileEntry(name: string, type = "text/plain"): FakeEntry {
  return {
    isFile: true,
    isDirectory: false,
    name,
    file: (cb) => cb(new File(["x"], name, { type })),
  };
}

function dirEntry(name: string, children: FakeEntry[]): FakeEntry {
  let handed = false;
  return {
    isFile: false,
    isDirectory: true,
    name,
    createReader: () => ({
      // The real readEntries hands out at most ~100 per call and [] when drained.
      readEntries: (cb) => {
        if (handed) return cb([]);
        handed = true;
        cb(children);
      },
    }),
  };
}

function transfer(entries: FakeEntry[]): DataTransfer {
  return {
    items: entries.map((e) => ({ kind: "file", webkitGetAsEntry: () => e })),
    files: [],
  } as unknown as DataTransfer;
}

describe("readTransferEntries (#364)", () => {
  it("returns dropped top-level files flat", async () => {
    const out = await readTransferEntries(transfer([fileEntry("a.csv")]));
    expect(out.map((f) => f.name)).toEqual(["a.csv"]);
  });

  it("recurses a dropped folder, preserving the relative path", async () => {
    const dt = transfer([
      dirEntry("data", [fileEntry("a.csv"), dirEntry("sub", [fileEntry("b.csv")])]),
    ]);
    const out = await readTransferEntries(dt);
    const rels = out
      .map((f) => (f as File & { webkitRelativePath?: string }).webkitRelativePath)
      .sort();
    expect(rels).toEqual(["data/a.csv", "data/sub/b.csv"]);
  });

  it("falls back to flat dt.files when the entries API is unavailable", async () => {
    const dt = { items: [], files: [new File(["x"], "z.txt")] } as unknown as DataTransfer;
    const out = await readTransferEntries(dt);
    expect(out.map((f) => f.name)).toEqual(["z.txt"]);
  });
});
