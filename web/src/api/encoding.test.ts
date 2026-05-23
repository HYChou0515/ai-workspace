import { describe, expect, it } from "vitest";

import { decodeBytes, encodeText } from "./encoding";

describe("file byte ↔ text encoding", () => {
  it("decodes valid UTF-8 as utf-8 and round-trips", () => {
    const bytes = new TextEncoder().encode("héllo · 文字 · 🚀");
    const { text, encoding } = decodeBytes(bytes);
    expect(encoding).toBe("utf-8");
    expect(text).toBe("héllo · 文字 · 🚀");
    expect(encodeText(text, encoding)).toEqual(bytes);
  });

  it("decodes non-UTF-8 bytes as binary (latin1) and round-trips losslessly", () => {
    // 0xFF 0xFE 0x00 0x80 is not valid UTF-8
    const bytes = new Uint8Array([0x00, 0x01, 0xff, 0xfe, 0x80, 0x7f, 0xc0]);
    const { text, encoding } = decodeBytes(bytes);
    expect(encoding).toBe("binary");
    expect(text.length).toBe(bytes.length);
    expect(encodeText(text, encoding)).toEqual(bytes);
  });

  it("preserves every byte value 0–255 through a binary round-trip", () => {
    const bytes = new Uint8Array(256);
    for (let i = 0; i < 256; i++) bytes[i] = i;
    const { text, encoding } = decodeBytes(bytes);
    expect(encoding).toBe("binary");
    expect(encodeText(text, encoding)).toEqual(bytes);
  });

  it("handles empty input", () => {
    const { text, encoding } = decodeBytes(new Uint8Array(0));
    expect(text).toBe("");
    expect(encoding).toBe("utf-8");
    expect(encodeText("", "utf-8")).toEqual(new Uint8Array(0));
  });
});
