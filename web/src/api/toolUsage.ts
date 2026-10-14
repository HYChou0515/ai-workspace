/**
 * The administrator's tool-usage report (docs/plan-tool-usage-stats.md §3.6),
 * and the same report of skill loads (§8). Superusers only — the server
 * answers 403 to anyone else.
 */
import { apiFetch, httpErrorFrom } from "./http";

export type UsageDays = 7 | 30 | 90;
/** What the report counts: tool calls, or skill loads (§8). */
export type UsageKind = "tool" | "skill";
export type ToolSource = "builtin" | "first_party" | "third_party";
export type SkillSource = "shared" | "profile" | "hub" | "workspace";

export type ToolUsageTool = {
  /** The tool's — or, for `kind=skill`, the skill's — name. */
  tool: string;
  source: ToolSource | SkillSource;
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
  ): Promise<ToolUsageReport> {
    const q = new URLSearchParams({ days: String(days) });
    for (const [k, v] of Object.entries(filter)) if (v) q.set(k, v);
    // Tools are the server's default — a caller that never names a kind is unchanged.
    if (kind !== "tool") q.set("kind", kind);
    const r = await apiFetch(`/admin/tool-usage?${q}`);
    if (!r.ok) throw await httpErrorFrom(r, `tool usage failed: ${r.status}`);
    return (await r.json()) as ToolUsageReport;
  },

  async detail(tool: string, days: UsageDays, kind: UsageKind = "tool"): Promise<ToolUsageDetail> {
    const k = kind === "tool" ? "" : `&kind=${kind}`;
    const r = await apiFetch(`/admin/tool-usage/${enc(tool)}?days=${days}${k}`);
    if (!r.ok) throw await httpErrorFrom(r, `tool usage failed: ${r.status}`);
    return (await r.json()) as ToolUsageDetail;
  },
};

export type ToolUsageClient = typeof toolUsageApi;
