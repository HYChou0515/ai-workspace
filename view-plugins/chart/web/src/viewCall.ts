/**
 * How `query` and `facet_build` are handed a view (#847/#848 P9).
 *
 * A view in a file names the FILE, as show_file's `validate` hook does. The
 * runner hands the args to the sandbox as ONE argv string, which the kernel
 * caps at 128 KiB (MAX_ARG_STRLEN), so the spec's text there let a big spec
 * pass show_file and get a 413 on every render; a path is the same size
 * whatever the spec is. `rev` is a digest of the text in hand, which the
 * sandbox ignores: the args are the run's cache key, so an edited file must be
 * a new call. A view with no file (a preview) still carries its text.
 */

/** cyrb53: a fast 53-bit string hash; a cache key, not a security boundary. */
function cyrb53(text: string): string {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 2654435761);
    h2 = Math.imul(h2 ^ c, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
}

export type ViewCall = { path: string; rev: string } | { spec: string };

export function viewCall(text: string, path: string | null | undefined): ViewCall {
  return path ? { path, rev: cyrb53(text) } : { spec: text };
}
