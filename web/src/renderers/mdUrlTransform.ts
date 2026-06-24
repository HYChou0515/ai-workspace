import { defaultUrlTransform } from "react-markdown";

import { API_PREFIX } from "../api/http";

/**
 * A react-markdown `urlTransform` that:
 *  1. leaves the given in-app scheme (`kb://`, `wiki://`) untouched — the
 *     component's link handler resolves those into in-app navigation;
 *  2. prepends the deploy base path (`API_PREFIX`) to root-relative URLs the BE
 *     emits in rendered markdown (e.g. `/blobs/{id}` image siblings), so they
 *     resolve under a path-stripping sub-path proxy (#73);
 *  3. otherwise defers to react-markdown's default sanitizing transform.
 */
export function baseAwareUrlTransform(preserveScheme: string) {
  return (url: string): string => {
    if (url.startsWith(preserveScheme)) return url;
    const safe = defaultUrlTransform(url);
    return safe.startsWith("/") && !safe.startsWith("//") ? API_PREFIX + safe : safe;
  };
}
