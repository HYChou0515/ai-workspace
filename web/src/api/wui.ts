/**
 * The WUI overview (`docs/plan-wui-overview.md`): the pages people Deployed.
 *
 * Deploy's last step writes a row (`deploy`); the overview page lists them
 * (`list`); Remove takes one off (`remove`). The listing is the server's, and
 * so is the filter — what comes back is exactly what this viewer could open by
 * address, never more.
 */

import { API_BASE, apiFetch, detailSentence, HttpError, httpErrorFrom } from "./http";
import { openAddress, type Reach, type Sharing } from "./publish";
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

/**
 * Where a listed page's link goes: a page that hands out copies opens through
 * the "preparing" page, which makes (or finds) the viewer's own copy (C12); a
 * page used directly is its own address. ONE answer for the card and the row.
 */
export function pageHref(page: { slug: string; item_id: string; path: string; sharing?: Sharing }): string {
  return page.sharing && page.sharing !== "direct"
    ? openAddress(page.slug, page.item_id, page.path)
    : wuiAddress(page.slug, page.item_id, page.path);
}

export type DeployedWui = {
  slug: string;
  item_id: string;
  item_title: string;
  /** The item's OWNER — not who pressed Deploy (`deployed_by`). */
  item_owner: string;
  path: string;
  title: string;
  deployed_by: string;
  deployed_at: number;
  /** The view file's `icon:` as the server stored it at Deploy — a file name
   * in the page's folder, an emoji, or a named-icon key — or `""` for none.
   * Resolved by `PageMark`; a form that does not resolve draws the default. */
  icon: string;
  /** The view file's `color:` as the server stored it at Deploy, or `""` —
   * the page's own colour for its card; the overview uses it when it is a
   * hex it can draw (`pageColour`) and the App's colour otherwise. */
  color: string;
  /** Who CREATED the item — the person its access is decided for;
   * `item_owner` is an editable display field (#687). The card's 「只有你」
   * compares the viewer with this. Optional: an older server omits it. */
  item_created_by?: string;
  /** The item's `permission` as its own GET returns it — `null` when it has
   * none (public). The card says who the link opens for from it
   * (`pageReach`, docs/plan-deploy-reach.md). Optional: a server from
   * before it sends nothing, and nothing is said. */
  item_permission?: unknown;
  /** Whether THIS viewer may Remove it — the server's `edit_content` answer,
   * so the button is drawn only where a press would be accepted. */
  can_remove: boolean;
  /** How copies are shared (`docs/plan-wui-publish-and-agent-hub.md` W2) —
   * "direct" for a page used in its own workspace, and for one Deployed
   * before Publish existed. Optional: an older server sends nothing. */
  sharing?: Sharing;
  /** Who may use a published page that hands out copies (C9); "" otherwise. */
  reach?: Reach | "";
  /** Usage (W4): opens and distinct people — never who. */
  opens?: number;
  people?: number;
  /** A published row (taken down with `publishApi.unpublish`), not an old
   * Deploy row (`wuiApi.remove`). */
  published?: boolean;
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
