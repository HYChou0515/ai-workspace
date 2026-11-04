/**
 * The administrator's report of deployed WUI pages (docs/plan-wui-usage.md U8):
 * person-days opened and each kind of use with its time, one page's people,
 * and the raw cells. Superusers only — the server answers 403 to anyone else.
 */
import { API_PREFIX, apiFetch, httpErrorFrom } from "./http";

/** Each kind's uses and milliseconds, and their sum (U3/U4). */
type Kinds = {
  workflow: number;
  workflow_ms: number;
  tool: number;
  tool_ms: number;
  agent: number;
  agent_ms: number;
  uses: number;
  uses_ms: number;
};

export type WuiUsagePage = Kinds & {
  item_id: string;
  /** The page's view file — its workspace path. */
  page: string;
  /** "" when the item no longer exists. */
  title: string;
  slug: string;
  person_days: number;
};

export type WuiUsageReport = { days: number; pages: WuiUsagePage[] };

export type WuiUsagePerson = Kinds & { user: string; person_days: number; opens: number };

export type WuiUsageDetail = { item_id: string; page: string; days: number; people: WuiUsagePerson[] };

export type WuiUsageClient = {
  report(days: number): Promise<WuiUsageReport>;
  detail(itemId: string, page: string, days: number): Promise<WuiUsageDetail>;
  /** Where the raw data downloads from — a link, not a fetch. */
  rawUrl(days: number): string;
};

const enc = encodeURIComponent;

export const wuiUsageApi: WuiUsageClient = {
  async report(days) {
    const r = await apiFetch(`/admin/wui-usage?days=${days}`);
    if (!r.ok) throw await httpErrorFrom(r, `page usage failed: ${r.status}`);
    return (await r.json()) as WuiUsageReport;
  },
  async detail(itemId, page, days) {
    const r = await apiFetch(`/admin/wui-usage/detail?item_id=${enc(itemId)}&page=${enc(page)}&days=${days}`);
    if (!r.ok) throw await httpErrorFrom(r, `page usage failed: ${r.status}`);
    return (await r.json()) as WuiUsageDetail;
  },
  rawUrl(days) {
    return `${API_PREFIX}/admin/wui-usage.csv?days=${days}`;
  },
};
