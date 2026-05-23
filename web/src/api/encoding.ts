/**
 * Byte ↔ text codec for the file editor. Valid UTF-8 decodes as "utf-8";
 * anything else falls back to "binary" (latin1), which maps each byte to a
 * U+0000–U+00FF code point so every byte round-trips losslessly. This lets
 * any file — even a true binary — be opened and edited in the text editor
 * without corrupting bytes the user didn't touch.
 */

export type FileEncoding = "utf-8" | "binary";

export function decodeBytes(bytes: Uint8Array): { text: string; encoding: FileEncoding } {
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return { text, encoding: "utf-8" };
  } catch {
    // True ISO-8859-1: byte → U+00XX, 1:1 and reversible. (TextDecoder's
    // "latin1" label is actually windows-1252, which is NOT byte-exact.)
    let text = "";
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      text += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
    }
    return { text, encoding: "binary" };
  }
}

export function encodeText(text: string, encoding: FileEncoding): Uint8Array {
  if (encoding === "binary") {
    const out = new Uint8Array(text.length);
    for (let i = 0; i < text.length; i++) out[i] = text.charCodeAt(i) & 0xff;
    return out;
  }
  return new TextEncoder().encode(text);
}
