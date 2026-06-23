/**
 * Encode/decode a collection leaf path (an open document or wiki page) for the
 * `documents/*` and `wiki/*` splat routes (#93). The identifier is a canonical
 * leading-slash file path that may contain slashes — so, unlike the slash-free
 * SourceDoc id in `kbLinks.docPath`, we must NOT `encodeURIComponent` the whole
 * thing (that would escape the separators). Instead the slashes stay real path
 * separators and only each segment is percent-encoded, so the URL reads
 * naturally: `/kb/collections/c1/documents/a%20dir/x.md`.
 */

import { normPath } from "../../api/kbFileService";

/** Canonical leaf path → splat tail: drop the leading slash, percent-encode
 * each segment, rejoin with "/". */
export function encodeLeafPath(path: string): string {
  return path
    .replace(/^\/+/, "")
    .split("/")
    .map(encodeURIComponent)
    .join("/");
}

/** Splat tail → canonical (leading-slash) leaf path. react-router already
 * percent-DECODES `params["*"]`, so we only re-add the leading slash via
 * normPath — decoding again would corrupt a literal "%" in a name. */
export function decodeLeafPath(splat: string): string {
  return normPath(splat);
}
