/**
 * Home main area — top bar, page header, tabs strip, real filter strip,
 * investigation table. All controls (search / filter / sort / pin / row
 * menu) are wired against the parent's `filters` + `pinned` state.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../../api";
import type { ActivityEntry, Investigation, Severity, Status } from "../../api/types";
import {
  formatInvestigationId,
  isCritical,
  relativeTime,
  summarize,
} from "../../api/types";
import { Icon } from "../../components/Icon";
import { Popover, PopoverDivider, PopoverItem } from "../../components/Popover";
import { SeverityChip, StatusChip } from "../../components/StatusChip";
import {
  type Filters,
  type HomeTab,
  type SortDir,
  type SortKey,
  applyFilters,
  countByStatus,
  criticalCount,
  filterByTab,
  isFiltersEmpty,
  openCount,
  ownedByCount,
  ownersOf,
  sortBy,
  togglePick,
  topicsOf,
  watchingCount,
} from "../home.helpers";

const SEVERITIES: Severity[] = ["P0", "P1", "P2", "P3", "P4"];
const STATUSES: Status[] = ["triaging", "awaiting_review", "resolved", "abandoned"];
const STATUS_LABEL: Record<Status, string> = {
  triaging: "Triaging",
  awaiting_review: "Awaiting review",
  resolved: "Resolved",
  abandoned: "Abandoned",
};
const SORT_LABEL: Record<SortKey, string> = {
  updated: "Updated",
  severity: "Severity",
  id: "ID",
  title: "Title",
};

export function HomeMain({
  items,
  currentUser,
  activeTab,
  onTab,
  filters,
  onFilters,
  sortKey,
  sortDir,
  onSort,
  pinned,
  togglePin,
  onOpenInvestigation,
}: {
  items: Investigation[];
  currentUser: string;
  activeTab: HomeTab;
  onTab: (tab: HomeTab) => void;
  filters: Filters;
  onFilters: (next: Filters) => void;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey, dir: SortDir) => void;
  pinned: ReadonlySet<string>;
  togglePin: (id: string) => void;
  onOpenInvestigation: (id: string) => void;
}) {
  const open = openCount(items);
  const critical = criticalCount(items);
  const byStatus = countByStatus(items);
  const owners = ownersOf(items);
  const topics = topicsOf(items);

  const rows = sortBy(
    applyFilters(filterByTab(items, activeTab, currentUser), filters),
    sortKey,
    sortDir,
    pinned,
  );

  const tabs: { key: HomeTab; label: string; count: number }[] = [
    { key: "all", label: "All", count: items.length },
    { key: "my_open", label: "My open", count: ownedByCount(items, currentUser) },
    { key: "watching", label: "Watching", count: watchingCount(items, currentUser) },
    { key: "triaging", label: "Triaging", count: byStatus.triaging },
    { key: "awaiting_review", label: "Awaiting review", count: byStatus.awaiting_review },
    { key: "resolved", label: "Resolved", count: byStatus.resolved },
    { key: "abandoned", label: "Abandoned", count: byStatus.abandoned },
  ];

  const setQuery = (q: string) => onFilters({ ...filters, query: q });
  const clearAll = () =>
    onFilters({ query: "", severities: [], owners: [], topics: [], products: [], statuses: [] });

  return (
    <main style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
      <TopBar query={filters.query} onQuery={setQuery} />

      <header style={{ padding: 28 }}>
        <div className="caps">Investigations</div>
        <h1
          style={{
            margin: "6px 0 0",
            fontFamily: "var(--font-display)",
            fontSize: "var(--text-display-lg)",
            lineHeight: "var(--leading-display-lg)",
            fontWeight: 800,
            letterSpacing: "-0.025em",
          }}
        >
          <span>{open}</span> open
          <span style={{ color: "var(--text-paper-d)", fontWeight: 700 }}> · </span>
          <span style={{ color: "var(--accent)" }}>{critical}</span> critical
        </h1>
        <div
          style={{
            marginTop: 4,
            color: "var(--text-paper-d)",
            fontSize: "var(--text-body)",
          }}
        >
          Investigations across all production lines, owners, and topics.
        </div>

        <div style={{ display: "flex", gap: 24, marginTop: 18 }}>
          <Metric label="Open" value={String(open)} sub="across all lines" />
          <Metric label="Critical" value={String(critical)} sub="P0 + P1, open" />
          <Metric label="Pinned" value={String(pinned.size)} sub="across all lines" />
        </div>
      </header>

      <div
        style={{
          display: "flex",
          gap: 4,
          padding: "0 28px",
          borderBottom: "1px solid var(--paper-3)",
        }}
      >
        {tabs.map((t) => {
          const active = t.key === activeTab;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => onTab(t.key)}
              style={{
                padding: "10px 12px",
                borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
                color: active ? "var(--text-paper)" : "var(--text-paper-d)",
                fontWeight: active ? 600 : 500,
                fontSize: "var(--text-body-sm)",
                marginBottom: -1,
                display: "inline-flex",
                gap: 6,
                alignItems: "baseline",
              }}
            >
              {t.label}
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: active ? "var(--accent)" : "var(--text-paper-d2)",
                }}
              >
                {t.count}
              </span>
            </button>
          );
        })}
      </div>

      <FilterStrip
        filters={filters}
        onFilters={onFilters}
        owners={owners}
        topics={topics}
        sortKey={sortKey}
        sortDir={sortDir}
        onSort={onSort}
        clearAll={clearAll}
      />

      <div className="scrollable" style={{ flex: 1, overflow: "auto" }}>
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: "var(--text-body-sm)",
          }}
        >
          <thead>
            <tr
              style={{
                position: "sticky",
                top: 0,
                background: "var(--paper)",
                borderBottom: "1px solid var(--paper-3)",
                textAlign: "left",
              }}
            >
              <Th width={32} />
              <Th width={120}>ID</Th>
              <Th>Investigation</Th>
              <Th width={88}>Severity</Th>
              <Th width={120}>Status</Th>
              <Th width={140}>Product</Th>
              <Th width={88}>Owner</Th>
              <Th width={100}>Updated</Th>
              <Th width={56} />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={9}
                  style={{ padding: 32, textAlign: "center", color: "var(--text-paper-d)" }}
                >
                  No investigations match this view.{" "}
                  {!isFiltersEmpty(filters) && (
                    <button
                      type="button"
                      onClick={clearAll}
                      style={{
                        color: "var(--accent-h)",
                        textDecoration: "underline",
                        background: "transparent",
                      }}
                    >
                      clear filters
                    </button>
                  )}
                </td>
              </tr>
            )}
            {rows.map((inv) => {
              const isPinned = pinned.has(inv.resource_id);
              return (
                <tr
                  key={inv.resource_id}
                  onClick={() => onOpenInvestigation(inv.resource_id)}
                  style={{
                    cursor: "pointer",
                    background: isCritical(inv.severity) ? "rgba(196,74,58,0.04)" : "transparent",
                    borderBottom: "1px solid var(--paper-3)",
                  }}
                >
                  <Td>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        togglePin(inv.resource_id);
                      }}
                      aria-label={isPinned ? "unpin" : "pin"}
                      title={isPinned ? "Unpin" : "Pin"}
                      style={{
                        color: isPinned ? "var(--accent)" : "var(--text-paper-d2)",
                        padding: 4,
                      }}
                    >
                      <Icon name="pin" size={14} />
                    </button>
                  </Td>
                  <Td mono>{formatInvestigationId(inv.resource_id)}</Td>
                  <Td>
                    <div style={{ fontWeight: 600 }}>{inv.title}</div>
                    <div style={{ color: "var(--text-paper-d)", fontSize: 12 }}>
                      {summarize(inv.description)}
                    </div>
                  </Td>
                  <Td><SeverityChip level={inv.severity} /></Td>
                  <Td><StatusChip status={inv.status} /></Td>
                  <Td>{inv.product || <span style={{ color: "var(--text-paper-d2)" }}>—</span>}</Td>
                  <Td>{inv.owner}</Td>
                  <Td mono>{relativeTime(inv.updated_time)}</Td>
                  <Td>
                    <RowMenu
                      pinned={isPinned}
                      onPin={() => togglePin(inv.resource_id)}
                      idText={formatInvestigationId(inv.resource_id)}
                    />
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </main>
  );
}

function TopBar({
  query,
  onQuery,
}: {
  query: string;
  onQuery: (q: string) => void;
}) {
  const [bellOpen, setBellOpen] = useState(false);

  // Close on Esc — even when the bell popover has no focused element.
  useEffect(() => {
    if (!bellOpen) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && setBellOpen(false);
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [bellOpen]);

  const focusAgent = () => {
    const composer = document.querySelector<HTMLTextAreaElement>(
      "[data-testid='agent-panel'] textarea",
    );
    if (composer) {
      composer.focus();
    } else {
      // No agent panel on Home — surface a hint instead.
      alert("Open an investigation to chat with the agent.");
    }
  };

  return (
    <div
      style={{
        height: 64,
        borderBottom: "1px solid var(--paper-3)",
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "0 28px",
        background: "var(--white)",
      }}
    >
      <label
        style={{
          width: 420,
          height: 36,
          border: "1px solid var(--paper-3)",
          borderRadius: "var(--radius-btn)",
          background: "var(--paper)",
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "0 12px",
          color: "var(--text-paper)",
          fontSize: "var(--text-body-sm)",
        }}
      >
        <Icon name="search" size={14} color="var(--text-paper-d)" />
        <input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Search investigations by title or id…"
          style={{
            flex: 1,
            border: 0,
            outline: "none",
            background: "transparent",
            fontSize: "var(--text-body-sm)",
          }}
        />
        {query && (
          <button
            type="button"
            onClick={() => onQuery("")}
            aria-label="clear search"
            style={{ color: "var(--text-paper-d)" }}
          >
            <Icon name="x" size={12} />
          </button>
        )}
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: "var(--text-paper-d2)",
            border: "1px solid var(--paper-3)",
            borderRadius: 4,
            padding: "0 6px",
          }}
        >
          ⌘K
        </span>
      </label>
      <span style={{ flex: 1 }} />
      <NotificationsBell />
      <button
        type="button"
        onClick={focusAgent}
        style={{
          height: 32,
          padding: "0 14px",
          borderRadius: "var(--radius-btn)",
          background: "var(--ink)",
          color: "var(--text-dark)",
          fontSize: "var(--text-body-sm)",
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <Icon name="sparkle" size={12} color="var(--accent)" />
        Ask agent
      </button>
    </div>
  );
}

const NOTIF_SEEN_KEY = "rca:notif-seen";

function NotificationsBell() {
  const navigate = useNavigate();
  const [items, setItems] = useState<ActivityEntry[]>([]);
  const [lastSeen, setLastSeen] = useState<string>(
    () => localStorage.getItem(NOTIF_SEEN_KEY) ?? "",
  );

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .listActivity()
        .then((a) => alive && setItems(a))
        .catch(() => undefined);
    void load();
    // light polling so the badge updates while the user lingers on Home
    const t = window.setInterval(load, 20_000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  const unread = items.filter((a) => a.ts > lastSeen).length;

  const markSeen = () => {
    const newest = items[0]?.ts ?? new Date().toISOString();
    setLastSeen(newest);
    try {
      localStorage.setItem(NOTIF_SEEN_KEY, newest);
    } catch {
      /* ignore */
    }
  };

  return (
    <Popover
      align="end"
      trigger={({ onClick, open }) => (
        <button
          type="button"
          onClick={() => {
            if (!open) markSeen(); // mark read as the panel opens
            onClick();
          }}
          title="Recent activity"
          style={{
            height: 32,
            padding: "0 10px",
            border: "1px solid var(--paper-3)",
            borderRadius: "var(--radius-btn)",
            fontSize: "var(--text-body-sm)",
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            background: open ? "var(--paper-2)" : "transparent",
          }}
        >
          <Icon name="bell" size={14} />
          {unread > 0 && (
            <span
              style={{
                background: "var(--accent)",
                color: "var(--white)",
                borderRadius: 8,
                padding: "0 5px",
                fontSize: 10,
                fontFamily: "var(--font-mono)",
              }}
            >
              {unread}
            </span>
          )}
        </button>
      )}
    >
      {(close) => (
        <div style={{ minWidth: 280, maxHeight: 360, overflowY: "auto", padding: "8px 4px" }}>
          <div className="caps" style={{ padding: "4px 10px" }}>
            Recent activity
          </div>
          {items.length === 0 && (
            <div style={{ padding: "8px 10px", color: "var(--text-paper-d)", fontSize: 12 }}>
              No activity yet.
            </div>
          )}
          {items.map((a, i) => (
            <NotifLine
              key={i}
              entry={a}
              onClick={() => {
                const id = a.ref.investigation_id;
                if (id) navigate(`/investigations/${id}`);
                close();
              }}
            />
          ))}
        </div>
      )}
    </Popover>
  );
}

function NotifLine({ entry, onClick }: { entry: ActivityEntry; onClick: () => void }) {
  const when = relativeTime(entry.ts);
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        width: "100%",
        textAlign: "left",
        padding: "6px 10px",
        display: "flex",
        gap: 8,
        fontSize: 12,
        color: "var(--text-paper)",
        background: "transparent",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--paper-2)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      <span
        style={{
          fontFamily: "var(--font-mono)",
          color: "var(--text-paper-d2)",
          width: 64,
          flexShrink: 0,
        }}
      >
        {when}
      </span>
      <span style={{ flex: 1 }}>{entry.text}</span>
    </button>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div>
      <div className="caps" style={{ fontSize: 10 }}>{label}</div>
      <div
        style={{
          fontFamily: "var(--font-display)",
          fontSize: "var(--text-display-sm)",
          fontWeight: 700,
          letterSpacing: "-0.02em",
          marginTop: 2,
        }}
      >
        {value}
      </div>
      <div style={{ color: "var(--text-paper-d2)", fontSize: 11 }}>{sub}</div>
    </div>
  );
}

function FilterStrip({
  filters,
  onFilters,
  owners,
  topics,
  sortKey,
  sortDir,
  onSort,
  clearAll,
}: {
  filters: Filters;
  onFilters: (next: Filters) => void;
  owners: string[];
  topics: string[];
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey, dir: SortDir) => void;
  clearAll: () => void;
}) {
  const empty = isFiltersEmpty(filters);
  return (
    <div
      style={{
        display: "flex",
        gap: 8,
        padding: "12px 28px",
        flexWrap: "wrap",
        alignItems: "center",
      }}
    >
      <Popover
        trigger={({ onClick, open }) => (
          <PickerBtn open={open} onClick={onClick}>
            Severity
            {filters.severities.length > 0 && (
              <CountBadge>{filters.severities.length}</CountBadge>
            )}
          </PickerBtn>
        )}
      >
        {() => (
          <div style={{ minWidth: 160 }}>
            {SEVERITIES.map((s) => (
              <PopoverItem
                key={s}
                selected={filters.severities.includes(s)}
                onClick={() =>
                  onFilters({
                    ...filters,
                    severities: togglePick(filters.severities, s),
                  })
                }
              >
                {s}
              </PopoverItem>
            ))}
          </div>
        )}
      </Popover>

      <Popover
        trigger={({ onClick, open }) => (
          <PickerBtn open={open} onClick={onClick}>
            Status
            {filters.statuses.length > 0 && (
              <CountBadge>{filters.statuses.length}</CountBadge>
            )}
          </PickerBtn>
        )}
      >
        {() => (
          <div style={{ minWidth: 200 }}>
            {STATUSES.map((s) => (
              <PopoverItem
                key={s}
                selected={filters.statuses.includes(s)}
                onClick={() =>
                  onFilters({
                    ...filters,
                    statuses: togglePick(filters.statuses, s),
                  })
                }
              >
                {STATUS_LABEL[s]}
              </PopoverItem>
            ))}
          </div>
        )}
      </Popover>

      <Popover
        trigger={({ onClick, open }) => (
          <PickerBtn open={open} onClick={onClick}>
            Topic
            {filters.topics.length > 0 && (
              <CountBadge>{filters.topics.length}</CountBadge>
            )}
          </PickerBtn>
        )}
      >
        {() => (
          <div style={{ minWidth: 200, maxHeight: 260, overflowY: "auto" }}>
            {topics.length === 0 && (
              <div style={{ padding: 10, fontSize: 12, color: "var(--text-paper-d)" }}>
                No topics yet.
              </div>
            )}
            {topics.map((t) => (
              <PopoverItem
                key={t}
                selected={filters.topics.includes(t)}
                onClick={() =>
                  onFilters({
                    ...filters,
                    topics: togglePick(filters.topics, t),
                  })
                }
              >
                {t}
              </PopoverItem>
            ))}
          </div>
        )}
      </Popover>

      <Popover
        trigger={({ onClick, open }) => (
          <PickerBtn open={open} onClick={onClick}>
            Owner
            {filters.owners.length > 0 && (
              <CountBadge>{filters.owners.length}</CountBadge>
            )}
          </PickerBtn>
        )}
      >
        {() => (
          <div style={{ minWidth: 180 }}>
            {owners.length === 0 && (
              <div style={{ padding: 10, fontSize: 12, color: "var(--text-paper-d)" }}>
                No owners.
              </div>
            )}
            {owners.map((o) => (
              <PopoverItem
                key={o}
                selected={filters.owners.includes(o)}
                onClick={() =>
                  onFilters({
                    ...filters,
                    owners: togglePick(filters.owners, o),
                  })
                }
              >
                {o}
              </PopoverItem>
            ))}
          </div>
        )}
      </Popover>

      {!empty && (
        <button
          type="button"
          onClick={clearAll}
          style={{
            padding: "0 8px",
            height: 28,
            color: "var(--accent-h)",
            fontSize: 12,
            border: "1px solid var(--accent)",
            borderRadius: "var(--radius-btn)",
            background: "var(--accent-soft)",
          }}
        >
          Clear filters
        </button>
      )}

      <span style={{ flex: 1 }} />

      <Popover
        align="end"
        trigger={({ onClick, open }) => (
          <PickerBtn open={open} onClick={onClick}>
            Sort: {SORT_LABEL[sortKey]} {sortDir === "asc" ? "↑" : "↓"}
          </PickerBtn>
        )}
      >
        {(close) => (
          <div style={{ minWidth: 180 }}>
            {(Object.keys(SORT_LABEL) as SortKey[]).map((k) => (
              <PopoverItem
                key={k}
                selected={sortKey === k}
                onClick={() => {
                  onSort(k, sortDir);
                  close();
                }}
              >
                {SORT_LABEL[k]}
              </PopoverItem>
            ))}
            <PopoverDivider />
            <PopoverItem
              selected={sortDir === "desc"}
              onClick={() => {
                onSort(sortKey, "desc");
                close();
              }}
            >
              Descending
            </PopoverItem>
            <PopoverItem
              selected={sortDir === "asc"}
              onClick={() => {
                onSort(sortKey, "asc");
                close();
              }}
            >
              Ascending
            </PopoverItem>
          </div>
        )}
      </Popover>
    </div>
  );
}

function PickerBtn({
  open,
  onClick,
  children,
}: {
  open: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        height: 28,
        padding: "0 10px",
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-btn)",
        fontSize: 12,
        color: "var(--text-paper)",
        background: open ? "var(--paper-2)" : "var(--white)",
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
      }}
    >
      {children}
      <Icon name="chev_d" size={11} color="var(--text-paper-d2)" />
    </button>
  );
}

function CountBadge({ children }: { children: React.ReactNode }) {
  return (
    <span
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        background: "var(--accent)",
        color: "var(--white)",
        borderRadius: 8,
        padding: "0 4px",
      }}
    >
      {children}
    </span>
  );
}

function RowMenu({
  pinned,
  onPin,
  idText,
}: {
  pinned: boolean;
  onPin: () => void;
  idText: string;
}) {
  return (
    <Popover
      align="end"
      width={180}
      trigger={({ onClick, open }) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onClick();
          }}
          aria-label="row menu"
          style={{
            color: open ? "var(--text-paper)" : "var(--text-paper-d)",
            padding: "4px 8px",
          }}
        >
          <Icon name="dots_h" size={14} />
        </button>
      )}
    >
      {(close) => (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{ minWidth: 180 }}
        >
          <PopoverItem
            onClick={() => {
              onPin();
              close();
            }}
          >
            {pinned ? "Unpin" : "Pin"}
          </PopoverItem>
          <PopoverItem
            onClick={() => {
              if (typeof navigator !== "undefined") {
                navigator.clipboard?.writeText(idText).catch(() => undefined);
              }
              close();
            }}
          >
            Copy ID
          </PopoverItem>
        </div>
      )}
    </Popover>
  );
}

function Th({
  children,
  width,
}: {
  children?: React.ReactNode;
  width?: number;
}) {
  return (
    <th
      style={{
        padding: "10px 12px",
        width,
        fontWeight: 600,
        fontSize: 11,
        color: "var(--text-paper-d)",
        textTransform: "uppercase",
        letterSpacing: "0.06em",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  mono = false,
}: {
  children?: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <td
      style={{
        padding: "12px",
        verticalAlign: "top",
        fontFamily: mono ? "var(--font-mono)" : undefined,
        fontSize: mono ? 12 : undefined,
        color: "var(--text-paper)",
      }}
    >
      {children}
    </td>
  );
}
