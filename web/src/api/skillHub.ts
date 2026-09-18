/**
 * The skill hub (`docs/plan-skill-hub.md`): skills people published, for
 * everyone to find and install.
 *
 * The listing and the detail are the server's — what comes back is exactly
 * what this viewer may read (an entry they may not is a 404, worded like one
 * that never existed). Installing happens IN AN ITEM (D2: the Skills panel),
 * never on the page; the page's only actions are the owner's, on their own
 * entry. The three tools the agent holds read the same store.
 */

import type { CollectionPermission } from "../lib/permission";
import { apiFetch, httpErrorFrom } from "./http";

export type SkillHubReviewVerdict = "ok" | "notes";
export type SkillUpstreamState = "live" | "unpublished" | "deleted";

/** One row of the list. `forks` is filled for a root; a fork's own is empty. */
export type SkillHubCard = {
  id: string;
  owner: string;
  name: string;
  description: string;
  source_app: string;
  referenced_tools: string[];
  /** The root's id when this is a fork, else "". */
  forked_from: string;
  review_verdict: SkillHubReviewVerdict;
  /** Owned by the signed-in viewer — the server's answer, not a client compare. */
  is_mine: boolean;
  /** `referenced_tools` minus the ceiling of the App the list was asked for
   * (`list(q, mine, app)`); empty when no App was asked about. */
  missing_tools: string[];
  forks: SkillHubCard[];
};

/** What a fork was forked from, as THIS viewer may know it: `owner` / `name`
 * only when the root is `live` for them. */
export type SkillHubLineage = {
  entry: string;
  state: SkillUpstreamState;
  owner: string;
  name: string;
};

export type SkillHubDetail = {
  id: string;
  owner: string;
  name: string;
  description: string;
  source_app: string;
  source_profile: string;
  /** The item it was published from — "" unless the viewer owns the entry. */
  source_item: string;
  referenced_tools: string[];
  review: { verdict: SkillHubReviewVerdict; notes: string[]; model: string };
  forked_from: SkillHubLineage | null;
  forks: SkillHubCard[];
  files: string[];
  skill_md: string;
  is_owner: boolean;
  visibility: "public" | "restricted" | "private";
  /** The full access state, for the owner's share dialog; `null` for others. */
  permission: CollectionPermission | null;
  /** `referenced_tools` minus the ceiling of the App asked about; empty when
   * no App was asked about. */
  missing_tools: string[];
};

/** Where the owner goes to edit (plan P8's table). `open`: go to `item_id`;
 * `new_item`: the source item cannot take the edit — `reason` says why — so
 * a new `app`/`profile` item is the way. */
export type SkillEditTarget = {
  action: "open" | "new_item";
  app: string;
  profile: string;
  item_id: string;
  reason: "" | "no_access" | "deleted" | "closed";
};

export type SkillInstalled = { name: string; missing_tools: string[] };

export type SkillHubApi = {
  /** `app` (a slug) adds each row's `missing_tools` against that App's ceiling. */
  list(q?: string, mine?: boolean, app?: string): Promise<SkillHubCard[]>;
  /** `app` (a slug) adds `missing_tools` against that App's ceiling. */
  get(entryId: string, app?: string): Promise<SkillHubDetail>;
  /** The Skills panel's install door: 409 when a folder of that name is
   * already in the item (the sentence names whose copy it is), 404 when the
   * entry cannot be read. */
  install(slug: string, itemId: string, entryId: string): Promise<SkillInstalled>;
  unpublish(entryId: string): Promise<void>;
  republish(entryId: string): Promise<void>;
  setPermission(entryId: string, perm: CollectionPermission): Promise<void>;
  remove(entryId: string): Promise<void>;
  transfer(entryId: string, owner: string): Promise<void>;
  edit(entryId: string): Promise<SkillEditTarget>;
};

const entryBase = (entryId: string) => `/skill-hub/entries/${encodeURIComponent(entryId)}`;

async function post(path: string, body?: unknown, failed = "request failed"): Promise<Response> {
  const resp = await apiFetch(path, {
    method: "POST",
    ...(body === undefined
      ? {}
      : { headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
  });
  if (!resp.ok) throw await httpErrorFrom(resp, `${failed}: ${resp.status}`);
  return resp;
}

export const skillHubApi: SkillHubApi = {
  async list(q = "", mine = false, app = "") {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (mine) params.set("mine", "true");
    if (app) params.set("app", app);
    const suffix = params.size ? `?${params}` : "";
    const resp = await apiFetch(`/skill-hub/entries${suffix}`);
    if (!resp.ok) throw await httpErrorFrom(resp, `skill hub listing failed: ${resp.status}`);
    return ((await resp.json()) as { entries: SkillHubCard[] }).entries;
  },
  async get(entryId, app) {
    const suffix = app ? `?app=${encodeURIComponent(app)}` : "";
    const resp = await apiFetch(`${entryBase(entryId)}${suffix}`);
    if (!resp.ok) throw await httpErrorFrom(resp, `skill hub entry failed: ${resp.status}`);
    return (await resp.json()) as SkillHubDetail;
  },
  async install(slug, itemId, entryId) {
    const resp = await post(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/skills/install`,
      { entry_id: entryId },
      "install failed",
    );
    return (await resp.json()) as SkillInstalled;
  },
  async unpublish(entryId) {
    await post(`${entryBase(entryId)}/unpublish`, undefined, "unpublish failed");
  },
  async republish(entryId) {
    await post(`${entryBase(entryId)}/republish`, undefined, "republish failed");
  },
  async setPermission(entryId, perm) {
    const resp = await apiFetch(`${entryBase(entryId)}/permission`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(perm),
    });
    if (!resp.ok) throw await httpErrorFrom(resp, `permission failed: ${resp.status}`);
  },
  async remove(entryId) {
    const resp = await apiFetch(entryBase(entryId), { method: "DELETE" });
    if (!resp.ok) throw await httpErrorFrom(resp, `delete failed: ${resp.status}`);
  },
  async transfer(entryId, owner) {
    await post(`${entryBase(entryId)}/transfer`, { owner }, "transfer failed");
  },
  async edit(entryId) {
    const resp = await post(`${entryBase(entryId)}/edit`, undefined, "edit failed");
    return (await resp.json()) as SkillEditTarget;
  },
};
