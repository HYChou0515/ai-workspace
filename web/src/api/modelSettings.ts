/**
 * The model settings page (`docs/plan-model-settings.md`): a superuser edits a
 * JSON Merge Patch over the deploy defaults — presets, every App's app.json and
 * profiles, the platform's model choices — saves it as a draft, and Applies it
 * as a version every process picks up within a second.
 *
 * Every route answers 404 to anyone who is not a superuser. A refusal to Apply
 * (or to take a key) is 422 with `{detail: {problems: [...]}}`, and arrives as
 * `HttpError.detail.problems` — read it with `problemsOf`.
 */

import { apiFetch, HttpError, httpErrorFrom } from "./http";

export type Patch = Record<string, unknown>;

/** The document the page edits, as the deploy ships it (secrets masked). */
export type SettingsDocument = {
  config?: Record<string, unknown>;
  apps?: Record<string, Record<string, unknown>>;
  profiles?: Record<string, Record<string, Record<string, unknown>>>;
};

export type ModelSettingsPage = {
  defaults: SettingsDocument;
  draft: Patch;
  published: Patch;
  version: string | null;
  /** Setting path -> "…" + the last four characters of a long key, "" for a
   * short one (shown as "set"). Never a value. */
  keys: Record<string, string>;
  /** Keys set or cleared since the last Apply — what the next Apply switches. */
  pending_keys: string[];
};

export type Applied = {
  version: string;
  patch: Patch;
  by: string;
  applied_at: number;
  previous: string | null;
  keys_changed: string[];
};

export type Connection = { ok: boolean; model: string; ms: number; reply: string; error: string };

const BASE = "/admin/model-settings";
const JSON_HEADERS = { "Content-Type": "application/json" };

async function send(path: string, method: string, body?: unknown): Promise<Response> {
  return apiFetch(`${BASE}${path}`, {
    method,
    headers: JSON_HEADERS,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const modelSettingsApi = {
  async get(): Promise<ModelSettingsPage> {
    const r = await apiFetch(BASE);
    if (!r.ok) throw await httpErrorFrom(r, `model settings failed: ${r.status}`);
    return (await r.json()) as ModelSettingsPage;
  },

  /** Save the draft; nothing running changes. Answers what would stop it running. */
  async saveDraft(patch: Patch): Promise<string[]> {
    const r = await send("/draft", "PUT", { patch });
    if (!r.ok) throw await httpErrorFrom(r, `saving the draft failed: ${r.status}`);
    return ((await r.json()) as { problems: string[] }).problems;
  },

  async check(patch: Patch): Promise<string[]> {
    const r = await send("/check", "POST", { patch });
    if (!r.ok) throw await httpErrorFrom(r, `checking failed: ${r.status}`);
    return ((await r.json()) as { problems: string[] }).problems;
  },

  async apply(): Promise<Applied> {
    const r = await send("/apply", "POST");
    if (!r.ok) throw await httpErrorFrom(r, `apply failed: ${r.status}`);
    return (await r.json()) as Applied;
  },

  async history(): Promise<Applied[]> {
    const r = await apiFetch(`${BASE}/history`);
    if (!r.ok) throw await httpErrorFrom(r, `history failed: ${r.status}`);
    return (await r.json()) as Applied[];
  },

  async rollback(version: string): Promise<Applied> {
    const r = await send("/rollback", "POST", { version });
    if (!r.ok) throw await httpErrorFrom(r, `rollback failed: ${r.status}`);
    return (await r.json()) as Applied;
  },

  async setKey(path: string, value: string): Promise<void> {
    const r = await send("/keys", "PUT", { path, value });
    if (!r.ok) throw await httpErrorFrom(r, `setting the key failed: ${r.status}`);
  },

  async clearKey(path: string): Promise<void> {
    const r = await apiFetch(`${BASE}/keys?path=${encodeURIComponent(path)}`, { method: "DELETE" });
    if (!r.ok) throw await httpErrorFrom(r, `clearing the key failed: ${r.status}`);
  },

  async testConnection(preset: string): Promise<Connection> {
    const r = await send("/test-connection", "POST", { preset });
    if (!r.ok) throw await httpErrorFrom(r, `test connection failed: ${r.status}`);
    return (await r.json()) as Connection;
  },
};

export type ModelSettingsClient = typeof modelSettingsApi;

/** What a refusal named, or [] when it was not that kind of refusal. */
export function problemsOf(error: unknown): string[] {
  if (!(error instanceof HttpError)) return [];
  const problems = error.detail?.problems;
  return Array.isArray(problems) ? problems.map(String) : [];
}
