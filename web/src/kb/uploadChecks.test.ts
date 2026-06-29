import { describe, expect, it } from "vitest";

import { mergeBlocked, screenFiles, type UploadCheckHint } from "./uploadChecks";

const OFFICE_HINT: UploadCheckHint = {
  id: "office_encryption",
  extensions: [".pptx", ".xlsx", ".docx"],
  forbid_magic_hex: ["d0cf11e0a1b11ae1"],
  message_key: "kb.upload.blocked.unreadable",
};

// OLE2/CFB signature (encrypted OOXML) vs a healthy ZIP (PK) header.
const OLE2 = new Uint8Array([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1, 0x00, 0x00]);
const ZIP = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x00, 0x00]);

function file(name: string, bytes: Uint8Array): File {
  // Cast around the DOM lib's strict BlobPart generic (ArrayBuffer vs
  // ArrayBufferLike); the bytes are a valid BufferSource at runtime.
  return new File([bytes as BlobPart], name);
}

describe("screenFiles (#325 client pre-block)", () => {
  it("blocks an encrypted Office file by its magic bytes", async () => {
    const { allowed, blocked } = await screenFiles([file("deck.pptx", OLE2)], [OFFICE_HINT]);
    expect(allowed).toEqual([]);
    expect(blocked).toHaveLength(1);
    expect(blocked[0].file.name).toBe("deck.pptx");
    expect(blocked[0].messageKey).toBe("kb.upload.blocked.unreadable");
  });

  it("allows a healthy ZIP-container Office file", async () => {
    const { allowed, blocked } = await screenFiles([file("deck.pptx", ZIP)], [OFFICE_HINT]);
    expect(allowed.map((f) => f.name)).toEqual(["deck.pptx"]);
    expect(blocked).toEqual([]);
  });

  it("ignores files whose extension no hint guards", async () => {
    // A .txt that happens to start with OLE2 bytes is not an Office upload.
    const { allowed, blocked } = await screenFiles([file("notes.txt", OLE2)], [OFFICE_HINT]);
    expect(allowed.map((f) => f.name)).toEqual(["notes.txt"]);
    expect(blocked).toEqual([]);
  });

  it("matches extension and magic case-insensitively", async () => {
    const { blocked } = await screenFiles([file("BOOK.XLSX", OLE2)], [OFFICE_HINT]);
    expect(blocked).toHaveLength(1);
  });

  it("partitions a mixed batch, preserving order", async () => {
    const files = [
      file("ok.docx", ZIP),
      file("locked.xlsx", OLE2),
      file("plain.md", OLE2),
    ];
    const { allowed, blocked } = await screenFiles(files, [OFFICE_HINT]);
    expect(allowed.map((f) => f.name)).toEqual(["ok.docx", "plain.md"]);
    expect(blocked.map((b) => b.file.name)).toEqual(["locked.xlsx"]);
  });

  it("accepts everything when there are no hints (server stays the gate)", async () => {
    const { allowed, blocked } = await screenFiles([file("deck.pptx", OLE2)], []);
    expect(allowed).toHaveLength(1);
    expect(blocked).toEqual([]);
  });
});

describe("mergeBlocked (#325)", () => {
  it("appends new entries", () => {
    const merged = mergeBlocked(
      [{ name: "a.pptx", messageKey: "k" }],
      [{ name: "b.xlsx", messageKey: "k" }],
    );
    expect(merged.map((b) => b.name)).toEqual(["a.pptx", "b.xlsx"]);
  });

  it("de-duplicates by name, the later entry winning", () => {
    const merged = mergeBlocked(
      [{ name: "a.pptx", messageKey: "old" }],
      [{ name: "a.pptx", messageKey: "new" }],
    );
    expect(merged).toEqual([{ name: "a.pptx", messageKey: "new" }]);
  });
});
