/**
 * Shared graceful-degradation view for the structured renderers (#361): when a
 * file is too large or won't parse, we never crash the viewer — we show the raw
 * bytes verbatim under a one-line notice. Reused by JsonTreeView / YamlTree /
 * JsonlView (whole-file fallback and per-record bad-line fallback).
 */

import { pxToRem } from "../lib/pxToRem";

/** utf-8 byte length of a string (the cap is measured in bytes, not chars, so
 * CJK-heavy files trip the same guard a browser would choke on). */
export function byteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

export function RawText({ text, note }: { text: string; note: string }) {
  return (
    <div style={{ height: "100%", minHeight: 0, overflow: "auto" }}>
      <div style={{ color: "var(--text-paper-d)", fontSize: pxToRem(12), padding: "6px 2px" }}>{note}</div>
      <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: pxToRem(12), margin: 0 }}>{text}</pre>
    </div>
  );
}
