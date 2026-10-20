/**
 * "Run as me" — who a page schedule runs AS (`docs/plan-wui-viewer-login.md`
 * Q7/Q8/Q12). A schedule has no request behind it, so it runs with nobody's
 * private values until a person lends theirs; binding always names the CALLER.
 */

import { apiFetch, httpErrorFrom } from "./http";
import type { RunsAsReason } from "./schedules";

export type BindingRow = {
  trigger_id: string;
  run: string;
  describe: string;
  /** Who it runs as; "" = nobody (the shared layer only). */
  bound_to: string;
  mine: boolean;
  /** Why it is them (`docs/plan-schedule-run-as.md` G8); "" for nobody. */
  reason: RunsAsReason;
  /** The superuser who named them, for `admin`. */
  assigned_by: string;
};

const base = (slug: string, itemId: string) =>
  `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/schedule-bindings`;
const q = (path: string) => `?path=${encodeURIComponent(path)}`;

export const scheduleBindingsApi = {
  async list(slug: string, itemId: string, path: string): Promise<BindingRow[]> {
    const r = await apiFetch(`${base(slug, itemId)}${q(path)}`);
    if (!r.ok) throw await httpErrorFrom(r, `schedules failed: ${r.status}`);
    return ((await r.json()) as { rows: BindingRow[] }).rows;
  },

  async bind(slug: string, itemId: string, path: string, key: string): Promise<void> {
    const r = await apiFetch(`${base(slug, itemId)}/${encodeURIComponent(key)}${q(path)}`, {
      method: "PUT",
    });
    if (!r.ok) throw await httpErrorFrom(r, `run as me failed: ${r.status}`);
  },

  /** A superuser names someone else (G5); they are told. */
  async assign(slug: string, itemId: string, path: string, key: string, user: string): Promise<void> {
    const r = await apiFetch(
      `${base(slug, itemId)}/${encodeURIComponent(key)}${q(path)}&user=${encodeURIComponent(user)}`,
      { method: "PUT" },
    );
    if (!r.ok) throw await httpErrorFrom(r, `choosing who it runs as failed: ${r.status}`);
  },

  async unbind(slug: string, itemId: string, path: string, key: string): Promise<void> {
    const r = await apiFetch(`${base(slug, itemId)}/${encodeURIComponent(key)}${q(path)}`, {
      method: "DELETE",
    });
    if (!r.ok) throw await httpErrorFrom(r, `stop running as me failed: ${r.status}`);
  },
};

export type ScheduleBindingsClient = typeof scheduleBindingsApi;
