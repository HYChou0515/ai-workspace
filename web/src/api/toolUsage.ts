/**
 * The administrator's tool-usage report (docs/plan-tool-usage-stats.md §3.6),
 * and the same report of skill loads (§8). Superusers only — the server
 * answers 403 to anyone else.
 */
import { API_PREFIX, apiFetch, httpErrorFrom } from "./http";

export type UsageDays = 7 | 30 | 90;
/** What the report counts: tool calls, or skill loads (§8). */
export type UsageKind = "tool" | "skill";
/** One row per command, or a package's commands added up (§9.1). */
export type UsageGroup = "command" | "package";
export type ToolSource = "builtin" | "first_party" | "third_party";
export type SkillSource = "shared" | "profile" | "hub" | "workspace";

export type ToolUsageTool = {
  /** The tool's — or, for `kind=skill`, the skill's — name. */
  tool: string;
  source: ToolSource | SkillSource;
  /** The package a command belongs to (§9.2); "" for a built-in or a skill. */
  package?: string;
  calls: number;
  fails: number;
  avg_ms: number;
};

export type ToolUsageReport = { days: number; tools: ToolUsageTool[] };

type Counts = { calls: number; fails: number; avg_ms: number };

export type ToolUsageDetail = {
  tool: string;
  days: number;
  by_user: (Counts & { user: string })[];
  by_surface_app: (Counts & { surface: string; app: string })[];
  /** `title` / `slug` are "" when the item no longer exists. */
  by_item: (Counts & { item: string; title: string; slug: string })[];
};

export type UsageFilter = { source?: string; surface?: string; app?: string };

const enc = encodeURIComponent;

export const toolUsageApi = {
  async report(
    days: UsageDays,
    filter: UsageFilter = {},
    kind: UsageKind = "tool",
    group: UsageGroup = "command",
  ): Promise<ToolUsageReport> {
    const q = new URLSearchParams({ days: String(days) });
    for (const [k, v] of Object.entries(filter)) if (v) q.set(k, v);
    // Tools and commands are the server's defaults — a caller that names
    // neither is unchanged.
    if (kind !== "tool") q.set("kind", kind);
    if (group !== "command") q.set("group", group);
    const r = await apiFetch(`/admin/tool-usage?${q}`);
    if (!r.ok) throw await httpErrorFrom(r, `tool usage failed: ${r.status}`);
    return (await r.json()) as ToolUsageReport;
  },

  async detail(
    tool: string,
    days: UsageDays,
    kind: UsageKind = "tool",
    group: UsageGroup = "command",
    /** The row's source — one name can come from two (§8.2); "" for all of them. */
    source = "",
    /** The row's package — one command name can be in two (review r3); omitted for all. */
    pkg?: string,
  ): Promise<ToolUsageDetail> {
    const k = kind === "tool" ? "" : `&kind=${kind}`;
    const g = group === "command" ? "" : `&group=${group}`;
    const s = source ? `&source=${enc(source)}` : "";
    const p = pkg === undefined ? "" : `&package=${enc(pkg)}`;
    const r = await apiFetch(`/admin/tool-usage/${enc(tool)}?days=${days}${k}${g}${s}${p}`);
    if (!r.ok) throw await httpErrorFrom(r, `tool usage failed: ${r.status}`);
    return (await r.json()) as ToolUsageDetail;
  },

  /** Where one kind's raw data downloads from (§9.3) — a link, not a fetch. */
  rawUrl(days: UsageDays, kind: UsageKind): string {
    return `${API_PREFIX}/admin/tool-usage.csv?days=${days}&kind=${kind}`;
  },
};

export type ToolUsageClient = typeof toolUsageApi;

/** What tells one report line from another: a name can come from two sources
 * (a skill), and a command name can be in two packages (two Apps). */
export function rowKey(r: Pick<ToolUsageTool, "tool" | "source" | "package">): string {
  return `${r.tool}\u0000${r.source}\u0000${r.package ?? ""}`;
}
