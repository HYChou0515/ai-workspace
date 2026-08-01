/**
 * "My resource usage" — what I am holding, and what I may hold.
 *
 * The limits refuse outright rather than evicting anything, so this is the only
 * way out of being at your limit: see what you hold, then release it. That makes
 * the panel part of the feature, not a report about it.
 *
 * Usage and limits arrive in ONE payload on purpose — fetching them separately
 * could render a pair that never coexisted, and "3 of 2 used" reads as a bug.
 */

import { apiFetch } from "./http";

/** One live sandbox charged to me. */
export type LiveEnvironment = {
  item_id: string;
  slug: string;
  title: string;
  cpu_cores: number;
  memory_bytes: number;
};

/** One item whose stored bytes are charged to me. */
export type OwnedWorkspace = {
  item_id: string;
  slug: string;
  title: string;
  bytes_used: number;
};

/** 0 in any dimension means "no limit" — the same sentinel the backend uses. */
export type ResourceLimits = {
  count: number;
  cpu: number;
  memory_bytes: number;
  disk_bytes: number;
};

export type MyResources = {
  owner: string;
  limits: ResourceLimits;
  live: LiveEnvironment[];
  workspaces: OwnedWorkspace[];
  cpu_in_use: number;
  memory_in_use: number;
  disk_in_use: number;
};

export type MyResourcesApi = {
  get(): Promise<MyResources>;
  closeEnvironment(itemId: string): Promise<void>;
};

export const myResourcesApi: MyResourcesApi = {
  async get() {
    const r = await apiFetch("/me/resources");
    return (await r.json()) as MyResources;
  },
  async closeEnvironment(itemId: string) {
    await apiFetch(`/me/resources/live/${encodeURIComponent(itemId)}`, {
      method: "DELETE",
    });
  },
};
