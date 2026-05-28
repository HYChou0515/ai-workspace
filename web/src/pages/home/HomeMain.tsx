/**
 * Home main area — top bar, page header, tabs strip, real filter strip,
 * investigation table. All controls (search / filter / sort / pin / row
 * menu) are wired against the parent's `filters` + `pinned` state.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { Investigation, NotificationItem, Severity, Status } from "../../api/types";
import { useNotifications } from "../../hooks/useNotifications";
import {
  formatInvestigationId,
  isCritical,
  isOpen,
  relativeTime,
  summarize,
} from "../../api/types";
import { Icon } from "../../components/Icon";
import { Popover, PopoverDivider, PopoverItem } from "../../components/Popover";
import { AskAgentLauncher } from "../kb/AskAgentLauncher";
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
  recent,
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
  recent: string[];
  togglePin: (id: string) => void;
  onOpenInvestigation: (id: string) => void;
}) {
  const open = openCount(items);
  const critical = criticalCount(items);
  const openP1 = items.filter((i) => isOpen(i.status) && i.severity === "P1").length;
  const byStatus = countByStatus(items);
  const owners = ownersOf(items);
  const topics = topicsOf(items);

  const rows = sortBy(
    applyFilters(filterByTab(items, activeTab, currentUser, { pinned, recent }), filters),
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
    <main
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        minWidth: 0,
        minHeight: 0,
      }}
    >
      <TopBar query={filters.query} onQuery={setQuery} />

      <header
        style={{
          padding: 28,
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          gap: 24,
        }}
      >
        <div>
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
            All investigations are visible to the org. Pin the ones you own.
          </div>
        </div>

        <div style={{ display: "flex", gap: 24, flexShrink: 0 }}>
          <Metric label="Open · P1" value={String(openP1)} sub="open, severity P1" />
          <Metric label="Critical" value={String(critical)} sub="P0 + P1, open" />
          <Metric label="Pinned" value={String(pinned.size)} sub="you follow" />
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

      <div className="scrollable" style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
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
              <Th>Investigation</Th>
              <Th width={140}>Product</Th>
              <Th width={180}>Topics</Th>
              <Th width={88}>Severity</Th>
              <Th width={120}>Status</Th>
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
                  <Td>
                    <div style={ellipsis(600, { fontWeight: 600 })}>{inv.title}</div>
                    <div style={ellipsis(600, { color: "var(--text-paper-d)", fontSize: 12 })}>
                      {summarize(inv.description)}
                    </div>
                  </Td>
                  <Td>{inv.product || <span style={{ color: "var(--text-paper-d2)" }}>—</span>}</Td>
                  <Td><TopicsCell topics={inv.topics} /></Td>
                  <Td><SeverityChip level={inv.severity} /></Td>
                  <Td><StatusChip status={inv.status} /></Td>
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
      <AskAgentLauncher />
    </div>
  );
}

function NotificationsBell() {
  const navigate = useNavigate();
  const { items, unread, markAllRead, markRead } = useNotifications();

  return (
    <Popover
      align="end"
      trigger={({ onClick, open }) => (
        <button
          type="button"
          onClick={onClick}
          title="Notifications"
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
        <div style={{ minWidth: 300, maxHeight: 380, overflowY: "auto", padding: "8px 4px" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "4px 10px",
            }}
          >
            <span className="caps">Notifications</span>
            {unread > 0 && (
              <button
                type="button"
                onClick={() => markAllRead()}
                style={{ fontSize: 11, color: "var(--text-paper-d)" }}
              >
                Mark all read
              </button>
            )}
          </div>
          {items.length === 0 && (
            <div style={{ padding: "8px 10px", color: "var(--text-paper-d)", fontSize: 12 }}>
              No notifications.
            </div>
          )}
          {items.map((n) => (
            <NotifLine
              key={n.resource_id}
              n={n}
              onClick={() => {
                markRead(n.resource_id);
                if (n.link) navigate(n.link);
                close();
              }}
            />
          ))}
        </div>
      )}
    </Popover>
  );
}

function NotifLine({ n, onClick }: { n: NotificationItem; onClick: () => void }) {
  const when = n.created_at ? relativeTime(new Date(n.created_at).toISOString()) : "";
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        width: "100%",
        textAlign: "left",
        padding: "8px 10px",
        display: "flex",
        gap: 8,
        fontSize: 12,
        color: "var(--text-paper)",
        background: n.read ? "transparent" : "var(--accent-soft)",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--paper-2)")}
      onMouseLeave={(e) =>
        (e.currentTarget.style.background = n.read ? "transparent" : "var(--accent-soft)")
      }
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: n.read ? 400 : 600 }}>{n.title}</div>
        {n.body && (
          <div
            style={{
              color: "var(--text-paper-d)",
              fontSize: 11,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {n.body}
          </div>
        )}
      </div>
      <span
        style={{ fontFamily: "var(--font-mono)", color: "var(--text-paper-d2)", flexShrink: 0 }}
      >
        {when}
      </span>
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
        whiteSpace: "nowrap", // nothing in the table wraps
      }}
    >
      {children}
    </td>
  );
}

/** Single-line cell content that truncates with an ellipsis past `max` px. */
function ellipsis(max: number, extra: React.CSSProperties = {}): React.CSSProperties {
  return {
    maxWidth: max,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    ...extra,
  };
}

/** Topics as compact pills, single-line, ellipsised past the column width. */
function TopicsCell({ topics }: { topics: string[] }) {
  if (topics.length === 0) return <span style={{ color: "var(--text-paper-d2)" }}>—</span>;
  return (
    <div style={{ display: "flex", gap: 4, ...ellipsis(168) }} title={topics.join(", ")}>
      {topics.map((t) => (
        <span
          key={t}
          style={{
            flexShrink: 0,
            padding: "1px 7px",
            borderRadius: 999,
            background: "var(--paper-2)",
            border: "1px solid var(--paper-3)",
            fontSize: 11,
            color: "var(--text-paper-d)",
          }}
        >
          {t}
        </span>
      ))}
    </div>
  );
}
