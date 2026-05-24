/**
 * kb:// link resolution. The backend rewrites a document's relative markdown
 * links to stable `kb://doc/{collection}/{user}/{path}` URIs (nothing
 * route-shaped is stored). The FE resolves them LATE: in the doc viewer's
 * markdown, a kb:// link becomes in-app navigation to that document; anything
 * else stays a normal link.
 */

import { API_BASE } from "../../api/http";

const KB_DOC_PREFIX = "kb://doc/";

/**
 * If `href` is a kb:// document URI, return its document id (the part after
 * `kb://doc/`, fragment stripped); otherwise null.
 */
export function parseKbDocHref(href: string): string | null {
  if (!href.startsWith(KB_DOC_PREFIX)) return null;
  const rest = href.slice(KB_DOC_PREFIX.length);
  const id = rest.split("#", 1)[0];
  return id.length > 0 ? id : null;
}

/**
 * The in-app ROUTE for a document's dedicated page (for react-router
 * `navigate`, which prepends the basename itself). The id is path-shaped
 * ({collection}/{user}/{path}); each segment is encoded but the slashes are
 * kept so the `/kb/doc/*` splat route reads it back. Optional cited passage
 * rides along as `?hl=` for highlighting.
 */
export function docPath(documentId: string, snippet?: string): string {
  const encoded = documentId.split("/").map(encodeURIComponent).join("/");
  const hl = snippet ? `?hl=${encodeURIComponent(snippet)}` : "";
  return `/kb/doc/${encoded}${hl}`;
}

/**
 * The full HREF for opening a document in a NEW TAB (`<a href target=_blank>`).
 * Unlike `docPath`, this includes the deploy sub-path prefix (API_BASE), since
 * a raw anchor doesn't go through the router's basename.
 */
export function docHref(documentId: string, snippet?: string): string {
  return API_BASE + docPath(documentId, snippet);
}
