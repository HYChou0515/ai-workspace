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
import { apiFetch, detailSentence, errorInfo, HttpError } from "./http";

export type SkillHubReviewVerdict = "ok" | "notes";
export type SkillUpstreamState = "live" | "unpublished" | "deleted";

/** One row of the list — flat: a fork is its own row (plan-skill-hub-ux-redo D2). */
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
  /** Installs and uses, summed over every pod's written counts (plan-skill-hub-history
   * §4.8). A fork counts its own; who installed or used it is never sent. */
  installs: number;
  uses: number;
  /** Direct forks the viewer may read — the row's 「N 個 fork」 link. */
  fork_count: number;
  /** A fork's original, named — `null` for an original, and for a fork whose
   * original the viewer may not read. */
  origin: { owner: string; name: string } | null;
  /** ISO time the content last changed; `null` for an entry from before it was recorded. */
  updated_at: string | null;
};

export type SkillHubSort = "name" | "popular" | "updated";

/** What the list page asks for. Browsing (no `q`, `mine` or `owner`) lists
 * originals only; any filter lists forks beside them. */
export type SkillHubBrowseQuery = {
  q?: string;
  mine?: boolean;
  owner?: string;
  sort?: SkillHubSort;
  offset?: number;
  limit?: number;
  /** An App slug: each row then says which of its tools that App lacks. */
  app?: string;
};

/** One page of the listing: its rows, how many match in all, and the day
 * counting began (`YYYY-MM-DD`, "" before anything was counted). */
export type SkillHubListing = { entries: SkillHubCard[]; total: number; counted_since: string };

/** One file of a version, listed (never read); `size` is `null` for an entry
 * from before versions were kept. */
export type SkillHubFile = { path: string; size: number | null };

/** A workspace holding a copy of the entry. */
export type SkillHubInstall = { app: string; item_id: string; title: string };

/** A workspace the install dialog offers, and what installing would do there. */
export type SkillHubTarget = {
  item_id: string;
  title: string;
  state: "ok" | "installed" | "name_taken";
  /** On `name_taken`: whose copy is in the way ("" for a hand-written folder). */
  owner: string;
};

export type SkillHubTargets = { missing_tools: string[]; items: SkillHubTarget[] };

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
  files: SkillHubFile[];
  /** How many files are under `scripts/`. */
  scripts: number;
  skill_md: string;
  is_owner: boolean;
  visibility: "public" | "restricted" | "private";
  /** The full access state, for the owner's share dialog; `null` for others. */
  permission: CollectionPermission | null;
  /** `referenced_tools` minus the ceiling of the App asked about; empty when
   * no App was asked about. */
  missing_tools: string[];
  installs: number;
  uses: number;
  counted_since: string;
  /** ISO time the content last changed; `null` for an entry from before it was recorded. */
  updated_at: string | null;
  /** The revision the entry is at — what `versionFile` takes to open one of
   * `files`; "" for an entry not in git yet. */
  revision: string;
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

/** One row of an entry's timeline (`docs/plan-skill-hub-history.md` §8),
 * newest first. `permission` rows reach the owner only — the server's call. */
export type SkillHubHistoryEvent = {
  revision: string;
  kind: "publish" | "rollback" | "transfer" | "permission";
  /** ISO time the revision was written. */
  at: string;
  /** Who did it (the owner at the time). */
  by: string;
  /** The owner after it — differs from `by` only on a transfer. */
  owner: string;
  /** The version current after it — compared, never shown. */
  commit: string;
  description: string;
  review_notes: string[];
  /** On a rollback: the revision that first published the version brought back. */
  to_revision: string;
  /** On a permission change: the visibility after it. */
  visibility: string;
  /** On a permission change: who may read it — `user:<id>` / `group:<id>`. */
  audience: string[];
  current: boolean;
  /** v1, v2, … on a publish or rollback; `null` on any other row. */
  version: number | null;
};

/** One version, read (never installed — G23). */
export type SkillHubVersion = {
  revision: string;
  commit: string;
  description: string;
  files: SkillHubFile[];
  scripts: number;
  skill_md: string;
};

/** One file of a version; `text` is `null` when the file is not text. */
export type SkillHubVersionFile = { path: string; text: string | null; size: number };

/** One file's difference; `patch` is `null` for a file that is not text. */
export type SkillHubFileChange = {
  path: string;
  status: "added" | "removed" | "changed";
  patch: string | null;
};

export type SkillHubApi = {
  /** `app` (a slug) adds each row's `missing_tools` against that App's ceiling. */
  list(q?: string, mine?: boolean, app?: string): Promise<SkillHubCard[]>;
  /** One page of the skill hub page's listing, in `sort` order. */
  browse(query: SkillHubBrowseQuery): Promise<SkillHubListing>;
  /** `app` (a slug) adds `missing_tools` against that App's ceiling. */
  get(entryId: string, app?: string): Promise<SkillHubDetail>;
  /** The viewer's workspaces that hold a copy of the entry. */
  installs(entryId: string): Promise<SkillHubInstall[]>;
  /** App `app`'s workspaces the viewer may install into, each with what
   * installing would do there. */
  targets(entryId: string, app: string): Promise<SkillHubTargets>;
  /** The Skills panel's install door: 409 when a folder of that name is
   * already in the item (a coded refusal naming whose copy it is), 404 when
   * the entry cannot be read. */
  install(slug: string, itemId: string, entryId: string): Promise<SkillInstalled>;
  unpublish(entryId: string): Promise<void>;
  republish(entryId: string): Promise<void>;
  setPermission(entryId: string, perm: CollectionPermission): Promise<void>;
  remove(entryId: string): Promise<void>;
  transfer(entryId: string, owner: string): Promise<void>;
  edit(entryId: string): Promise<SkillEditTarget>;
  /** The timeline, newest first. */
  history(entryId: string): Promise<SkillHubHistoryEvent[]>;
  version(entryId: string, revision: string): Promise<SkillHubVersion>;
  versionFile(entryId: string, revision: string, path: string): Promise<SkillHubVersionFile>;
  /** What changed from `from` to `to`, per file. */
  diff(entryId: string, from: string, to: string): Promise<SkillHubFileChange[]>;
  /** Owner only. `expected` is the commit the page showed as current: a 409
   * `version_moved` when someone published or rolled back since. */
  rollback(entryId: string, revision: string, expected: string): Promise<void>;
  /** 〔從這一版 fork〕: that version copied into an item; install's refusals. */
  fork(slug: string, itemId: string, entryId: string, revision: string): Promise<SkillInstalled>;
};

const entryBase = (entryId: string) => `/skill-hub/entries/${encodeURIComponent(entryId)}`;
const versionBase = (entryId: string, revision: string) =>
  `${entryBase(entryId)}/versions/${encodeURIComponent(revision)}`;

/** `files` as this client knows them. An API pod from before the files tab
 * (mid-rollout) sends bare names; they read as files of unknown size. */
function asFiles(files: unknown): SkillHubFile[] {
  return (Array.isArray(files) ? files : []).map((f) =>
    typeof f === "string" ? { path: f, size: null } : (f as SkillHubFile),
  );
}

async function getJson<T>(path: string, failed: string): Promise<T> {
  const resp = await apiFetch(path);
  if (!resp.ok) throw await refused(resp, failed);
  return (await resp.json()) as T;
}

/**
 * A refusal, as the page will word it. The hub's routes refuse with a CODE
 * and its parameters (`{"detail": {"error": "folder_in_the_way", "owner":
 * "alice", "path": ".skill/…"}}`, plan-skill-hub-ui-polish D16), kept on the
 * error for `describeRefusal` to word in the viewer's language; the message
 * is the fallback (what failed and the status) for a code nobody knows. A
 * server that still sends a sentence (`{"detail": "…"}`) gets it as the
 * message, as before — `httpErrorFrom` alone was the wrong helper here, it
 * drops a string `detail`, so every refusal read as "install failed: 409"
 * (review round 1).
 */
async function refused(resp: Response, failed: string): Promise<HttpError> {
  const fallback = `${failed} (${resp.status})`;
  const info = await errorInfo(resp);
  if (info.code)
    return new HttpError(
      resp.status,
      fallback,
      info.code,
      info.also,
      info.detail,
    );
  const sentence = await detailSentence(resp);
  return new HttpError(resp.status, sentence ?? fallback);
}

async function post(path: string, body?: unknown, failed = "request failed"): Promise<Response> {
  const resp = await apiFetch(path, {
    method: "POST",
    ...(body === undefined
      ? {}
      : { headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
  });
  if (!resp.ok) throw await refused(resp, failed);
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
    if (!resp.ok) throw await refused(resp, "the skill hub listing failed");
    return ((await resp.json()) as { entries: SkillHubCard[] }).entries;
  },
  async browse({ q = "", mine = false, owner = "", sort = "name", offset = 0, limit, app = "" }) {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (mine) params.set("mine", "true");
    if (owner) params.set("owner", owner);
    if (sort !== "name") params.set("sort", sort);
    if (offset) params.set("offset", String(offset));
    if (limit) params.set("limit", String(limit));
    if (app) params.set("app", app);
    const suffix = params.size ? `?${params}` : "";
    const resp = await apiFetch(`/skill-hub/entries${suffix}`);
    if (!resp.ok) throw await refused(resp, "the skill hub listing failed");
    const body = (await resp.json()) as Partial<SkillHubListing>;
    const entries = body.entries ?? [];
    return {
      entries,
      // An API pod from before paging (mid-rollout) sends no total: what it
      // sent is all there is. 0 would draw "nobody has published anything".
      total: body.total ?? offset + entries.length,
      counted_since: body.counted_since ?? "",
    };
  },
  async get(entryId, app) {
    const suffix = app ? `?app=${encodeURIComponent(app)}` : "";
    const resp = await apiFetch(`${entryBase(entryId)}${suffix}`);
    if (!resp.ok) throw await refused(resp, "the skill hub entry could not be read");
    const body = (await resp.json()) as SkillHubDetail;
    return { ...body, files: asFiles(body.files), scripts: body.scripts ?? 0 };
  },
  async installs(entryId) {
    const body = await getJson<{ installs: SkillHubInstall[] }>(
      `${entryBase(entryId)}/installs`,
      "the installs could not be read",
    );
    return body.installs;
  },
  targets(entryId, app) {
    return getJson<SkillHubTargets>(
      `${entryBase(entryId)}/targets?${new URLSearchParams({ app })}`,
      "the workspaces could not be read",
    );
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
    if (!resp.ok) throw await refused(resp, "the visibility could not be saved");
  },
  async remove(entryId) {
    const resp = await apiFetch(entryBase(entryId), { method: "DELETE" });
    if (!resp.ok) throw await refused(resp, "delete failed");
  },
  async transfer(entryId, owner) {
    await post(`${entryBase(entryId)}/transfer`, { owner }, "transfer failed");
  },
  async edit(entryId) {
    const resp = await post(`${entryBase(entryId)}/edit`, undefined, "edit failed");
    return (await resp.json()) as SkillEditTarget;
  },
  async history(entryId) {
    const body = await getJson<{ events: SkillHubHistoryEvent[] }>(
      `${entryBase(entryId)}/history`,
      "the history could not be read",
    );
    return body.events;
  },
  async version(entryId, revision) {
    const body = await getJson<SkillHubVersion>(
      versionBase(entryId, revision),
      "the version could not be read",
    );
    return { ...body, files: asFiles(body.files), scripts: body.scripts ?? 0 };
  },
  versionFile(entryId, revision, path) {
    const q = new URLSearchParams({ path });
    return getJson<SkillHubVersionFile>(
      `${versionBase(entryId, revision)}/file?${q}`,
      "the file could not be read",
    );
  },
  async diff(entryId, from, to) {
    const q = new URLSearchParams({ from, to });
    const body = await getJson<{ files: SkillHubFileChange[] }>(
      `${entryBase(entryId)}/diff?${q}`,
      "the comparison failed",
    );
    return body.files;
  },
  async rollback(entryId, revision, expected) {
    await post(`${entryBase(entryId)}/rollback`, { revision, expected }, "rollback failed");
  },
  async fork(slug, itemId, entryId, revision) {
    const resp = await post(
      `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}/skills/fork`,
      { entry_id: entryId, revision },
      "fork failed",
    );
    return (await resp.json()) as SkillInstalled;
  },
};
