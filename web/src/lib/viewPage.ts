/**
 * The editor-area-only page (#847 Q5.3): `/a/{slug}/{itemId}/view` with either
 * `?layout=<json>` (a `show_file(layout=…)` arrangement) or `?path=<file>`.
 *
 * Where chat mode sends a shown file: the workspace is folded there, so the
 * card opens this page in a new tab — panes and views, no file tree, no chat —
 * instead of the raw content URL (which showed a `.ai.yaml` as YAML text).
 */
import { isLayoutNode, type LayoutNode } from "../pages/investigation/paneTree";

export type ViewPageTarget = { layout: LayoutNode } | { path: string };

export function viewPageHref(slug: string, itemId: string, target: ViewPageTarget): string {
  const q =
    "layout" in target
      ? new URLSearchParams({ layout: JSON.stringify(target.layout) })
      : new URLSearchParams({ path: target.path });
  // The workspace route decodes its id (`AppWorkspace`), so encode it here.
  return `/a/${slug}/${encodeURIComponent(itemId)}/view?${q.toString()}`;
}

/** What the page's query asks to show, or `null` when it names nothing it can
 * draw. A URL is untrusted input, so a layout is validated like a declaration. */
export function readViewPageTarget(search: URLSearchParams): ViewPageTarget | null {
  const raw = search.get("layout");
  if (raw) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return null;
    }
    return isLayoutNode(parsed) ? { layout: parsed } : null;
  }
  const path = search.get("path");
  return path ? { path } : null;
}
