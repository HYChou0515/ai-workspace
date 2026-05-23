/**
 * Aggregations for the Home page — counts, filters, topic groupings.
 * Pure functions; UI imports these and renders the numbers.
 */

import type { Investigation, Severity, Status } from "../api/types";
import { formatInvestigationId, isCritical, isOpen } from "../api/types";

export type HomeTab =
  | "all"
  | "pinned"
  | "recently_viewed"
  | "my_open"
  | "watching"
  | "triaging"
  | "awaiting_review"
  | "resolved"
  | "abandoned";

export type SortKey = "updated" | "severity" | "id" | "title";
export type SortDir = "asc" | "desc";

export type Filters = {
  /** Free-text match on title or formatted id. */
  query: string;
  severities: Severity[];
  owners: string[];
  topics: string[];
  products: string[];
  statuses: Status[];
};

export const EMPTY_FILTERS: Filters = {
  query: "",
  severities: [],
  owners: [],
  topics: [],
  products: [],
  statuses: [],
};

export function isFiltersEmpty(f: Filters): boolean {
  return (
    f.query.trim() === "" &&
    f.severities.length === 0 &&
    f.owners.length === 0 &&
    f.topics.length === 0 &&
    f.products.length === 0 &&
    f.statuses.length === 0
  );
}

const SEVERITY_RANK: Record<Severity, number> = {
  P0: 0,
  P1: 1,
  P2: 2,
  P3: 3,
  P4: 4,
};

export function openCount(items: Investigation[]): number {
  return items.filter((i) => isOpen(i.status)).length;
}

export function criticalCount(items: Investigation[]): number {
  return items.filter((i) => isCritical(i.severity) && isOpen(i.status)).length;
}

export function countByStatus(items: Investigation[]): Record<Status, number> {
  const out: Record<Status, number> = {
    triaging: 0,
    awaiting_review: 0,
    resolved: 0,
    abandoned: 0,
  };
  for (const inv of items) out[inv.status] += 1;
  return out;
}

export function ownedByCount(items: Investigation[], user: string): number {
  return items.filter((i) => i.owner === user && isOpen(i.status)).length;
}

export function watchingCount(items: Investigation[], user: string): number {
  return items.filter((i) => i.members.includes(user) && isOpen(i.status)).length;
}

/**
 * Topic → { total, active }. `active` is the # of open investigations
 * tagged with the topic — drives the green/grey dot in the sidebar.
 */
export function topicCounts(
  items: Investigation[],
): Map<string, { total: number; active: number }> {
  const out = new Map<string, { total: number; active: number }>();
  for (const inv of items) {
    for (const topic of inv.topics) {
      const entry = out.get(topic) ?? { total: 0, active: 0 };
      entry.total += 1;
      if (isOpen(inv.status)) entry.active += 1;
      out.set(topic, entry);
    }
  }
  return out;
}

export function filterByTab(
  items: Investigation[],
  tab: HomeTab,
  currentUser: string,
  ctx?: { pinned?: ReadonlySet<string>; recent?: string[] },
): Investigation[] {
  switch (tab) {
    case "all":
      return items;
    case "pinned":
      return items.filter((i) => ctx?.pinned?.has(i.resource_id));
    case "recently_viewed": {
      const order = ctx?.recent ?? [];
      const rank = new Map(order.map((id, i) => [id, i]));
      return items
        .filter((i) => rank.has(i.resource_id))
        .sort((a, b) => (rank.get(a.resource_id)! - rank.get(b.resource_id)!));
    }
    case "my_open":
      return items.filter((i) => i.owner === currentUser && isOpen(i.status));
    case "watching":
      return items.filter(
        (i) => i.members.includes(currentUser) && isOpen(i.status),
      );
    case "triaging":
      return items.filter((i) => i.status === "triaging");
    case "awaiting_review":
      return items.filter((i) => i.status === "awaiting_review");
    case "resolved":
      return items.filter((i) => i.status === "resolved");
    case "abandoned":
      return items.filter((i) => i.status === "abandoned");
  }
}

/** Distinct owners across the dataset, sorted alphabetically. */
export function ownersOf(items: Investigation[]): string[] {
  const set = new Set<string>();
  for (const i of items) set.add(i.owner);
  return [...set].sort();
}

/** Distinct topics across the dataset, sorted alphabetically. */
export function topicsOf(items: Investigation[]): string[] {
  const set = new Set<string>();
  for (const i of items) for (const t of i.topics) set.add(t);
  return [...set].sort();
}

/** Apply free-text + multi-select filters. */
export function applyFilters(
  items: Investigation[],
  f: Filters,
): Investigation[] {
  const q = f.query.trim().toLowerCase();
  return items.filter((i) => {
    if (q) {
      const hay = `${i.title} ${formatInvestigationId(i.resource_id)}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (f.severities.length > 0 && !f.severities.includes(i.severity)) return false;
    if (f.statuses.length > 0 && !f.statuses.includes(i.status)) return false;
    if (f.owners.length > 0 && !f.owners.includes(i.owner)) return false;
    if (f.topics.length > 0) {
      // any-of: at least one of the investigation's topics must be selected
      if (!i.topics.some((t) => f.topics.includes(t))) return false;
    }
    if (f.products.length > 0 && !f.products.includes(i.product)) return false;
    return true;
  });
}

/** Toggle a value in/out of a string list (multi-select pickers). */
export function togglePick<T extends string>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export function sortBy(
  items: Investigation[],
  key: SortKey,
  dir: SortDir = "desc",
  pinned: ReadonlySet<string> = new Set(),
): Investigation[] {
  const sign = dir === "asc" ? 1 : -1;
  const copy = [...items];
  copy.sort((a, b) => {
    // Pinned always wins, regardless of sort key.
    const aPin = pinned.has(a.resource_id);
    const bPin = pinned.has(b.resource_id);
    if (aPin !== bPin) return aPin ? -1 : 1;
    switch (key) {
      case "updated":
        return sign * (new Date(a.updated_time).getTime() - new Date(b.updated_time).getTime());
      case "severity":
        return sign * (SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]) * -1; // P0 first when desc
      case "id":
        return sign * a.resource_id.localeCompare(b.resource_id);
      case "title":
        return sign * a.title.localeCompare(b.title);
    }
  });
  return copy;
}
