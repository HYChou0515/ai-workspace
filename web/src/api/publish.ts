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
  /** The copies' sandbox size the publisher chose (C10); absent from servers
   * before round 1 of review. */
  cpu_cores?: number | null;
  memory_bytes?: number | null;
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
  /** How it is published now — `null` when it is not (C9/C10: a re-publish
   * starts from it). */
  current(slug: string, itemId: string, path: string): Promise<Published | null>;
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
  async current(slug, itemId, path) {
    const resp = await apiFetch(`${itemBase(slug, itemId)}?path=${encodeURIComponent(path)}`);
    if (resp.status === 404) return null;
    if (!resp.ok) throw await httpErrorFrom(resp, `publication failed: ${resp.status}`);
    return (await resp.json()) as Published;
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

/** Is there a newer publish than the one this copy was made from?
 * (`docs/plan-wui-copy-update.md`, `api/publish_update.py`). */
export type CopyUpdate = {
  due: boolean;
  sharing: Sharing | "";
  published_at: number;
  /** The publication — what 前往新版 opens, and the page a new copy opens at. */
  source_item_id: string;
  path: string;
  /** The release notes since this copy was made, oldest first. */
  notes: { at: number; by: string; text: string }[];
  /** D5: what a new copy writes and the room for it (`free` null: no cap). */
  needs: number;
  free: number | null;
  free_if_replaced: number | null;
  fits: boolean;
};

export type CopyUpdateApi = {
  status(slug: string, itemId: string): Promise<CopyUpdate>;
  /** A new copy from the latest snapshot; ``replace`` deletes this one (D5). */
  take(slug: string, itemId: string, opts: { replace: boolean }): Promise<{ slug: string; item_id: string }>;
};

const updateBase = (slug: string, itemId: string) =>
  `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/published-update`;

export const copyUpdateApi: CopyUpdateApi = {
  async status(slug, itemId) {
    const resp = await apiFetch(updateBase(slug, itemId));
    if (!resp.ok) throw await httpErrorFrom(resp, `update check failed: ${resp.status}`);
    return (await resp.json()) as CopyUpdate;
  },
  async take(slug, itemId, opts) {
    const resp = await apiFetch(updateBase(slug, itemId), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(opts),
    });
    if (!resp.ok) throw await httpErrorFrom(resp, `taking the new version failed: ${resp.status}`);
    return (await resp.json()) as { slug: string; item_id: string };
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
