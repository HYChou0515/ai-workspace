/**
 * The PRIVATE env layer (`docs/plan-wui-viewer-login.md`): the values a person
 * keeps for one item's tools — typed, filled by a login, or written for them by
 * the deploy's own seam. Nobody else can read them, a superuser included.
 *
 * The routes address "mine" and take no user, so this client has no way to
 * name anyone else — the shape is the guarantee.
 */

import { apiFetch, httpErrorFrom } from "./http";

const base = (slug: string, itemId: string) =>
  `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/env/private`;

export const privateEnvApi = {
  async get(slug: string, itemId: string): Promise<Record<string, string>> {
    const r = await apiFetch(base(slug, itemId));
    if (!r.ok) throw await httpErrorFrom(r, `private env failed: ${r.status}`);
    return ((await r.json()) as { values: Record<string, string> }).values;
  },

  /** The whole set: a name left out is removed. */
  async put(slug: string, itemId: string, values: Record<string, string>): Promise<void> {
    const r = await apiFetch(base(slug, itemId), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    });
    if (!r.ok) throw await httpErrorFrom(r, `saving your values failed: ${r.status}`);
  },

  /** Log out: forget every value the caller keeps for this item. */
  async clear(slug: string, itemId: string): Promise<void> {
    const r = await apiFetch(base(slug, itemId), { method: "DELETE" });
    if (!r.ok) throw await httpErrorFrom(r, `logging out failed: ${r.status}`);
  },
};

export type PrivateEnvClient = typeof privateEnvApi;
