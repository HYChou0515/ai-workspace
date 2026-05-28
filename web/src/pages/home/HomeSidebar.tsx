/**
 * Home left sidebar (240px). Header lockup + new-investigation button,
 * nav list, topics, footer user. Driven by aggregated state.
 */

import { formatInvestigationId, type Investigation } from "../../api/types";
import { Icon, type IconName } from "../../components/Icon";
import { RcaLockup } from "../../components/RcaMark";
import { SettingsButton } from "../../components/SettingsButton";
import { UserAvatar } from "../../components/UserChip";
import { useUser } from "../../hooks/useUsers";
import {
  type Filters,
  type HomeTab,
  countByStatus,
  ownedByCount,
  topicCounts,
  togglePick,
  watchingCount,
} from "../home.helpers";

type ExtraNav = "pinned" | "recently_viewed" | "templates";

type NavItem = {
  key: HomeTab | ExtraNav;
  label: string;
  count?: number;
};

export function HomeSidebar({
  items,
  currentUser,
  activeTab,
  onTab,
  pinned,
  recent,
  filters,
  onFilters,
  onNewInvestigation,
  onOpenTemplates,
  onOpenInvestigation,
  onOpenKnowledge,
  onOpenChats,
}: {
  items: Investigation[];
  currentUser: string;
  activeTab: HomeTab;
  onTab: (tab: HomeTab) => void;
  pinned: ReadonlySet<string>;
  recent: string[];
  filters: Filters;
  onFilters: (next: Filters) => void;
  onNewInvestigation: () => void;
  onOpenTemplates: () => void;
  onOpenInvestigation: (id: string) => void;
  /** Jump to the KB collections surface (the global knowledge base). */
  onOpenKnowledge: () => void;
  /** Jump to the KB chat-history surface. */
  onOpenChats: () => void;
}) {
  const byStatus = countByStatus(items);
  const openTotal = byStatus.triaging + byStatus.awaiting_review;
  const myOpen = ownedByCount(items, currentUser);
  const watching = watchingCount(items, currentUser);
  const topics = topicCounts(items);
  const me = useUser(currentUser);

  // Helper sets to enable a couple of synthetic sidebar items: pinned
  // (apply filter to just pinned ids) and recently viewed (top of recent).
  const pinnedItems = items.filter((i) => pinned.has(i.resource_id));
  const recentInvIds = recent.filter((id) => items.some((i) => i.resource_id === id));

  const navItems: NavItem[] = [
    { key: "all", label: "All open", count: openTotal },
    { key: "pinned", label: "Pinned", count: pinnedItems.length },
    { key: "my_open", label: "Owned by me", count: myOpen },
    { key: "watching", label: "Watching", count: watching },
    { key: "recently_viewed", label: "Recently viewed", count: recentInvIds.length },
    { key: "resolved", label: "Resolved (30d)", count: byStatus.resolved },
    { key: "abandoned", label: "Abandoned (30d)", count: byStatus.abandoned },
    { key: "templates", label: "Templates" },
  ];

  const onNavClick = (key: HomeTab | ExtraNav) => {
    if (key === "templates") {
      onOpenTemplates();
      return;
    }
    // "pinned" and "recently_viewed" are now real HomeTab filter views.
    onTab(key);
  };

  return (
    <aside
      style={{
        width: 240,
        background: "var(--paper)",
        borderRight: "1px solid var(--paper-3)",
        display: "flex",
        flexDirection: "column",
        flexShrink: 0,
      }}
    >
      <header style={{ padding: "20px 18px 16px", borderBottom: "1px solid var(--paper-3)" }}>
        <RcaLockup size={40} />
        <button
          type="button"
          onClick={onNewInvestigation}
          style={{
            marginTop: 16,
            width: "100%",
            height: 36,
            background: "var(--accent)",
            color: "var(--white)",
            borderRadius: "var(--radius-btn)",
            fontWeight: 500,
            fontSize: "var(--text-body-sm)",
          }}
        >
          + New investigation
        </button>
      </header>

      <nav
        className="scrollable"
        // minHeight: 0 — without it `flex:1` defers to content's intrinsic
        // height and overflowY:auto never kicks in (sidebar pushes the shell
        // past 100vh → page scrollbar).
        style={{ padding: 8, flex: 1, minHeight: 0, overflowY: "auto" }}
      >
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {navItems.map((n) => {
            const active = n.key === activeTab;
            return (
              <li key={n.key}>
                <button
                  type="button"
                  onClick={() => onNavClick(n.key)}
                  style={{
                    width: "100%",
                    textAlign: "left",
                    padding: "6px 10px",
                    borderRadius: 4,
                    background: active ? "var(--accent-soft)" : "transparent",
                    color: active ? "var(--accent-h)" : "var(--text-paper)",
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: "var(--text-body-sm)",
                  }}
                >
                  <span>{n.label}</span>
                  {typeof n.count === "number" && (
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        color: active ? "var(--accent-h)" : "var(--text-paper-d2)",
                      }}
                    >
                      {n.count}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>

        <Section caps="Knowledge base">
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 2 }}>
            <li>
              <KbNavLink icon="layers" label="Knowledge" onClick={onOpenKnowledge} />
            </li>
            <li>
              <KbNavLink icon="chat" label="Chat" onClick={onOpenChats} />
            </li>
          </ul>
        </Section>

        {pinnedItems.length > 0 && (
          <Section caps="Pinned">
            {pinnedItems.map((inv) => (
              <SidebarLink
                key={inv.resource_id}
                onClick={() => onOpenInvestigation(inv.resource_id)}
                primary={inv.title}
                secondary={formatInvestigationId(inv.resource_id)}
              />
            ))}
          </Section>
        )}

        {recentInvIds.length > 0 && (
          <Section caps="Recently viewed">
            {recentInvIds.slice(0, 6).map((id) => {
              const inv = items.find((i) => i.resource_id === id);
              if (!inv) return null;
              return (
                <SidebarLink
                  key={id}
                  onClick={() => onOpenInvestigation(id)}
                  primary={inv.title}
                  secondary={formatInvestigationId(inv.resource_id)}
                />
              );
            })}
          </Section>
        )}

        <Section caps="Topics">
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 2 }}>
            {[...topics.entries()].map(([topic, { total, active }]) => {
              const selected = filters.topics.includes(topic);
              return (
                <li key={topic}>
                  <button
                    type="button"
                    onClick={() =>
                      onFilters({
                        ...filters,
                        topics: togglePick(filters.topics, topic),
                      })
                    }
                    style={{
                      width: "100%",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "4px 10px",
                      fontSize: "var(--text-body-sm)",
                      color: selected ? "var(--accent-h)" : "var(--text-paper)",
                      borderRadius: 4,
                      background: selected ? "var(--accent-soft)" : "transparent",
                      textAlign: "left",
                    }}
                    title={selected ? "Click to remove topic filter" : "Click to filter by topic"}
                  >
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <span
                        style={{
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          background: active > 0 ? "var(--accent)" : "var(--paper-3)",
                        }}
                      />
                      {topic}
                    </span>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        color: selected ? "var(--accent-h)" : "var(--text-paper-d2)",
                      }}
                    >
                      {total}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </Section>
      </nav>

      <footer
        style={{
          padding: "12px 14px",
          borderTop: "1px solid var(--paper-3)",
          background: "var(--paper-2)",
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <UserAvatar userId={currentUser} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: "var(--text-body-sm)", fontWeight: 500 }}>{me.name}</div>
          <div style={{ fontSize: 11, color: "var(--text-paper-d)" }}>
            {me.section || "Process engineer"}
          </div>
        </div>
        <SettingsButton />
      </footer>
    </aside>
  );
}

function Section({
  caps,
  children,
}: {
  caps: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginTop: 16, padding: "0 4px" }}>
      <div
        className="caps"
        style={{ margin: "6px 10px", fontSize: "var(--text-mono-caps)" }}
      >
        {caps}
      </div>
      {children}
    </div>
  );
}

function KbNavLink({
  icon,
  label,
  onClick,
}: {
  icon: IconName;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        width: "100%",
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 10px",
        borderRadius: 4,
        background: "transparent",
        color: "var(--text-paper)",
        fontSize: "var(--text-body-sm)",
        textAlign: "left",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "var(--paper-2)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "transparent";
      }}
    >
      <Icon name={icon} size={15} color="var(--text-paper-d)" />
      {label}
    </button>
  );
}

function SidebarLink({
  onClick,
  primary,
  secondary,
}: {
  onClick: () => void;
  primary: string;
  secondary: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        width: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        alignItems: "flex-start",
        padding: "4px 10px",
        textAlign: "left",
        borderRadius: 4,
        background: "transparent",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "var(--paper-2)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "transparent";
      }}
    >
      <span
        style={{
          fontSize: 12,
          color: "var(--text-paper)",
          maxWidth: "100%",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {primary}
      </span>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-paper-d2)" }}>
        {secondary}
      </span>
    </button>
  );
}

