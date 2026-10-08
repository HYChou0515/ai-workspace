/**
 * My environment variables (`docs/plan-personal-env.md`): a person's values for
 * every item. An item uses a name from here only when its policy asks for a
 * personal value (Private first / Private only); its own value for that item
 * still wins.
 *
 * The routes address "mine" and take no user, so this client has no way to
 * name anyone else — the shape is the guarantee.
 */

import type { EnvProvider } from "./types";
import { apiFetch, httpErrorFrom } from "./http";

export type PersonalValues = {
  values: Record<string, string>;
  /** When each name last got a new value (epoch ms). */
  updated: Record<string, number>;
};

export const personalEnvApi = {
  async get(): Promise<PersonalValues> {
    const r = await apiFetch("/me/env");
    if (!r.ok) throw await httpErrorFrom(r, `my environment variables failed: ${r.status}`);
    return (await r.json()) as PersonalValues;
  },

  /** The whole set: a name left out is removed. */
  async put(values: Record<string, string>): Promise<PersonalValues> {
    const r = await apiFetch("/me/env", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    });
    if (!r.ok) throw await httpErrorFrom(r, `saving my environment variables failed: ${r.status}`);
    return (await r.json()) as PersonalValues;
  },

  /** The deploy's sign-ins, with no item to ask about. */
  async providers(): Promise<EnvProvider[]> {
    const r = await apiFetch("/me/env-providers");
    if (!r.ok) throw await httpErrorFrom(r, `sign-ins failed: ${r.status}`);
    return ((await r.json()) as { providers: EnvProvider[] }).providers;
  },

  /** Run one sign-in. `values` carries the credential and is never stored;
   * what comes back is the product, for the caller to save. */
  async resolve(providerId: string, values: Record<string, string>): Promise<Record<string, string>> {
    const r = await apiFetch(`/me/env-providers/${encodeURIComponent(providerId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    });
    // Through `httpErrorFrom`: the implementation wrote a sentence for the
    // person ("your password is wrong"), and that is what they should see.
    if (!r.ok) throw await httpErrorFrom(r, `sign-in ${providerId} refused`);
    return ((await r.json()) as { env: Record<string, string> }).env;
  },
};

export type PersonalEnvClient = typeof personalEnvApi;

/** Every write is "read the row, PUT the whole row", so two at once would read
 * the same row and the second would undo the first. Mutations sharing this
 * scope run one after another — the page's and the Env panel's alike (round 2,
 * F2). */
export const PERSONAL_ENV_WRITES = { id: "personal-env-writes" } as const;
