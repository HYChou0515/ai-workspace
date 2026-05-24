/**
 * kb:// link resolution. The backend rewrites a document's relative markdown
 * links to stable `kb://doc/{collection}/{user}/{path}` URIs (nothing
 * route-shaped is stored). The FE resolves them LATE: in the doc viewer's
 * markdown, a kb:// link becomes in-app navigation to that document; anything
 * else stays a normal link.
 */

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
