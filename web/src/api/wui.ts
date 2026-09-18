/**
 * The WUI overview (`docs/plan-wui-overview.md`): the pages people Deployed.
 *
 * Deploy's last step writes a row (`deploy`); the overview page lists them
 * (`list`); Remove takes one off (`remove`). The listing is the server's, and
 * so is the filter — what comes back is exactly what this viewer could open by
 * address, never more.
 */

import { API_BASE, apiFetch, detailSentence, HttpError, httpErrorFrom } from "./http";
import { encodePath } from "./refPath";

/**
 * A page's own address, origin-relative — what `WuiPage` answers at
 * `/w/:slug/:itemId/*` (`App.tsx`). Under the DEPLOY BASE (`API_BASE`, "" or
 * "/my-svc/rca"): the router mounts there, and a link that started at the
 * origin left the SPA on every sub-path deploy. Slug and item id encoded the
 * way `itemCallTool` encodes them; the path segment by segment, so a folder
 * with a space or a CJK name still round-trips through the router's decoding.
 *
 * ONE spelling: the pane's Deploy panel and the overview's rows both link
 * here, and two copies would be two addresses the moment one changed.
 */
export function wuiAddress(slug: string, itemId: string, path: string): string {
  return `${API_BASE}/w/${encodeURIComponent(slug)}/${encodeURIComponent(itemId)}/${encodePath(path)}`;
}

export type DeployedWui = {
  slug: string;
  item_id: string;
  item_title: string;
  path: string;
  title: string;
  deployed_by: string;
  deployed_at: number;
  /** The view file's `icon:` as the server stored it at Deploy — a file name
   * in the page's folder, an emoji, or a named-icon key — or `""` for none.
   * Resolved by `PageMark`; a form that does not resolve draws the default. */
  icon: string;
  /** Whether THIS viewer may Remove it — the server's `edit_content` answer,
   * so the button is drawn only where a press would be accepted. */
  can_remove: boolean;
};

export type WuiApi = {
  /** List `path` (the view file, workspace-absolute) on the overview. Rejects
   * with an `HttpError` carrying the server's sentence — a 403 is "not
   * authorized to edit_content", a 400 names what the file turned out to be. */
  deploy(slug: string, itemId: string, path: string, signal?: AbortSignal): Promise<DeployedWui>;
  remove(slug: string, itemId: string, path: string): Promise<void>;
  list(): Promise<DeployedWui[]>;
};

const itemBase = (slug: string, itemId: string) =>
  `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/wui/deploy`;

export const wuiApi: WuiApi = {
  async deploy(slug, itemId, path, signal) {
    const resp = await apiFetch(itemBase(slug, itemId), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ path }),
      signal,
    });
    if (!resp.ok) {
      const detail = await detailSentence(resp);
      throw new HttpError(resp.status, detail ?? `The page could not be listed (${resp.status}).`);
    }
    return (await resp.json()) as DeployedWui;
  },
  async remove(slug, itemId, path) {
    const resp = await apiFetch(`${itemBase(slug, itemId)}?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    });
    if (!resp.ok) throw await httpErrorFrom(resp, `remove failed: ${resp.status}`);
  },
  async list() {
    const resp = await apiFetch("/wui");
    if (!resp.ok) throw await httpErrorFrom(resp, `WUI overview failed: ${resp.status}`);
    return ((await resp.json()) as { pages: DeployedWui[] }).pages;
  },
};
