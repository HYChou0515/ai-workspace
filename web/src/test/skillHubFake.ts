/**
 * A stand-in for the skill hub's API with the server's listing rules
 * (`list_skill_hub`): browsing lists originals with their fork counts, any
 * filter lists forks beside them; `offset`/`limit` page it and `total` counts
 * every match. Every other call is a bare mock the test fills in.
 */
import { vi } from "vitest";

import type { SkillHubApi, SkillHubBrowseQuery, SkillHubCard } from "../api/skillHub";

export const hubCard = (over: Partial<SkillHubCard>): SkillHubCard => ({
  id: "e-root",
  owner: "alice",
  name: "triage-reflow",
  description: "Triage reflow defects.",
  source_app: "rca",
  referenced_tools: ["exec"],
  forked_from: "",
  review_verdict: "ok",
  is_mine: false,
  missing_tools: [],
  installs: 0,
  uses: 0,
  fork_count: 0,
  origin: null,
  updated_at: null,
  ...over,
});

/** The rows a query matches, in the server's order, before paging. */
export function matching(entries: SkillHubCard[], query: SkillHubBrowseQuery): SkillHubCard[] {
  const { q = "", mine = false, owner = "", sort = "name" } = query;
  const needle = q.trim().toLowerCase();
  const browsing = !needle && !mine && !owner;
  const ids = new Set(entries.map((e) => e.id));
  const rows = entries
    .filter((e) => !mine || e.is_mine)
    .filter((e) => !owner || e.owner === owner)
    .filter(
      (e) =>
        !needle ||
        e.name.toLowerCase().includes(needle) ||
        e.description.toLowerCase().includes(needle),
    )
    .filter((e) => !(browsing && ids.has(e.forked_from)))
    .map((e) => ({ ...e, fork_count: entries.filter((f) => f.forked_from === e.id).length }));
  if (sort === "popular") rows.sort((a, b) => b.uses - a.uses || b.installs - a.installs);
  if (sort === "updated")
    rows.sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
  return rows;
}

export function fakeSkillHub(entries: SkillHubCard[] = [], since = "2026-10-07") {
  return {
    list: vi.fn<SkillHubApi["list"]>(async (q = "", mine = false) =>
      matching(entries, { q, mine }),
    ),
    browse: vi.fn<SkillHubApi["browse"]>(async (query) => {
      const rows = matching(entries, query);
      const offset = query.offset ?? 0;
      return {
        entries: rows.slice(offset, offset + (query.limit ?? 50)),
        total: rows.length,
        counted_since: since,
      };
    }),
    get: vi.fn<SkillHubApi["get"]>(),
    installs: vi.fn<SkillHubApi["installs"]>(async () => []),
    targets: vi.fn<SkillHubApi["targets"]>(async () => ({ missing_tools: [], items: [] })),
    install: vi.fn<SkillHubApi["install"]>(),
    unpublish: vi.fn<SkillHubApi["unpublish"]>(),
    republish: vi.fn<SkillHubApi["republish"]>(),
    setPermission: vi.fn<SkillHubApi["setPermission"]>(),
    remove: vi.fn<SkillHubApi["remove"]>(),
    transfer: vi.fn<SkillHubApi["transfer"]>(),
    edit: vi.fn<SkillHubApi["edit"]>(),
    history: vi.fn<SkillHubApi["history"]>(async () => []),
    version: vi.fn<SkillHubApi["version"]>(),
    versionFile: vi.fn<SkillHubApi["versionFile"]>(),
    diff: vi.fn<SkillHubApi["diff"]>(async () => []),
    rollback: vi.fn<SkillHubApi["rollback"]>(),
    fork: vi.fn<SkillHubApi["fork"]>(),
  } satisfies SkillHubApi;
}
