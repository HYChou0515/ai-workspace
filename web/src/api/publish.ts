/**
 * Publishing a WUI or an agent (`docs/plan-wui-publish-and-agent-hub.md`).
 *
 * One server core for both — a `Published` row and, for every way of sharing
 * that hands out copies, a snapshot of the workspace — and two things to
 * people: a WUI is published from its page pane and found on `/wui`; an
 * agent is published from its agent panel and found in Agent Hub.
 */

import { API_BASE, apiFetch, detailSentence, HttpError, httpErrorFrom } from "./http";
import { encodePath } from "./refPath";

export type PublishKind = "wui" | "agent";
/** How copies are shared: a WUI's four (W2) and an agent's one (A2). */
export type Sharing = "fresh" | "per_person" | "shared" | "direct" | "per_conversation";
export type Reach = "me" | "subjects" | "everyone";
export type Role = "reader" | "participant" | "collaborator";

export const WUI_SHARINGS: readonly Sharing[] = ["fresh", "per_person", "shared", "direct"];

export type PublishChoices = {
  sharing: Sharing;
  reach: Reach;
  /** `user:<id>` / `group:<id>` when `reach` is "subjects". */
  subjects: string[];
  /** "One shared by everyone" only (W3). */
  role: Role;
  /** The copies' sandbox size (C10); null ⇒ the App's. */
  cpu_cores: number | null;
  memory_bytes: number | null;
  /** A re-publish's "what changed" (A9). */
  note: string;
};

export type Published = {
  id: string;
  kind: PublishKind;
  slug: string;
  item_id: string;
  path: string;
  title: string;
  sharing: Sharing;
  reach: Reach;
  subjects: string[];
  role: Role;
  published_by: string;
  published_at: number;
  icon: string;
  color: string;
  description: string;
  files: number;
  bytes: number;
  based_on_title: string;
  based_on_by: string;
};

export type FolderSize = { folder: string; files: number; bytes: number };
export type Estimate = { files: number; bytes: number; biggest: FolderSize[] };
export type Opened = {
  slug: string;
  item_id: string;
  created: boolean;
  /** What was opened — where the browser goes next. */
  kind: PublishKind;
};

export type PublishApi = {
  publish(
    slug: string,
    itemId: string,
    path: string,
    choices: PublishChoices,
    signal?: AbortSignal,
  ): Promise<Published>;
  unpublish(slug: string, itemId: string, path: string): Promise<void>;
  estimate(slug: string, itemId: string, path: string): Promise<Estimate>;
  open(itemId: string, path: string): Promise<Opened>;
};

const itemBase = (slug: string, itemId: string) =>
  `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/publish`;

export const publishApi: PublishApi = {
  async publish(slug, itemId, path, choices, signal) {
    const resp = await apiFetch(itemBase(slug, itemId), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ path, ...choices }),
      signal,
    });
    if (!resp.ok) {
      const detail = await detailSentence(resp);
      throw new HttpError(resp.status, detail ?? `It could not be published (${resp.status}).`);
    }
    return (await resp.json()) as Published;
  },
  async unpublish(slug, itemId, path) {
    const resp = await apiFetch(`${itemBase(slug, itemId)}?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    });
    if (!resp.ok) throw await httpErrorFrom(resp, `unpublish failed: ${resp.status}`);
  },
  async estimate(slug, itemId, path) {
    const resp = await apiFetch(`${itemBase(slug, itemId)}/estimate?path=${encodeURIComponent(path)}`);
    if (!resp.ok) throw await httpErrorFrom(resp, `estimate failed: ${resp.status}`);
    return (await resp.json()) as Estimate;
  },
  async open(itemId, path) {
    const resp = await apiFetch("/published/open", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ item_id: itemId, path }),
    });
    if (!resp.ok) throw await httpErrorFrom(resp, `open failed: ${resp.status}`);
    return (await resp.json()) as Opened;
  },
};

/**
 * The address that opens a published WUI or agent for whoever follows it:
 * the "preparing" page (C12), which makes (or finds) their copy and then
 * takes them into it. Under the deploy base like `wuiAddress`. A WUI shared
 * "directly" has no copy to make, and links to the page itself instead.
 */
export function openAddress(slug: string, itemId: string, path: string): string {
  return `${API_BASE}/open/${encodeURIComponent(slug)}/${encodeURIComponent(itemId)}/${encodePath(path)}`;
}
