/**
 * kb:// link resolution. The backend rewrites a document's relative markdown
 * links to stable `kb://doc/{id}` URIs, where `{id}` is the opaque, slash-free
 * SourceDoc id. The FE resolves them LATE: in the doc viewer's markdown, a
 * kb:// link becomes in-app navigation to that document; anything else stays a
 * normal link.
 */

import { API_BASE, API_PREFIX } from "../../api/http";

const KB_DOC_PREFIX = "kb://doc/";

/**
 * If `href` is a kb:// document URI, return its document id (the part after
 * `kb://doc/`, fragment stripped); otherwise null.
 */
export function parseKbDocHref(href: string): string | null {
  if (!href.startsWith(KB_DOC_PREFIX)) return null;
  const rest = href.slice(KB_DOC_PREFIX.length);
  const id = rest.split("#", 1)[0];
  if (!id) return null;
  // The markdown renderer percent-encodes the non-ASCII ∕ (U+2215) the doc id
  // uses as its separator, so the href arrives as `…%E2%88%95…`. Decode it back
  // to the RAW id — every consumer (docPath route, renderDocument) re-encodes
  // exactly once, so returning the encoded form here double-encodes → 404.
  try {
    return decodeURIComponent(id);
  } catch {
    return id; // malformed escape — hand back what we have
  }
}

/**
 * The in-app ROUTE for a document's dedicated page (for react-router
 * `navigate`, which prepends the basename itself). The id is an opaque,
 * slash-free token — encode it as one segment; the `/kb/doc/*` splat route
 * reads it back. Optional cited passage rides along as `?hl=` for highlighting.
 */
export function docPath(documentId: string, snippet?: string): string {
  const hl = snippet ? `?hl=${encodeURIComponent(snippet)}` : "";
  return `/kb/doc/${encodeURIComponent(documentId)}${hl}`;
}

/**
 * The full HREF for opening a document in a NEW TAB (`<a href target=_blank>`).
 * Unlike `docPath`, this includes the deploy sub-path prefix (API_BASE), since
 * a raw anchor doesn't go through the router's basename.
 */
export function docHref(documentId: string, snippet?: string): string {
  return API_BASE + docPath(documentId, snippet);
}

/**
 * The download HREF for a document's ORIGINAL bytes — specstar serves the blob
 * at `GET /blobs/{file_id}` (file_id = the content hash on the SourceDoc).
 */
export function blobHref(fileId: string): string {
  return `${API_PREFIX}/blobs/${encodeURIComponent(fileId)}`;
}
