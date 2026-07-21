/**
 * App dashboard (`/a/:slug`) — one App's home, modelled on the design-handoff
 * `home.jsx` (#89): a left sidebar (brand + create + saved-view nav + topics +
 * user) and a main area (page header with summary + metric cards, status tabs,
 * a filter strip, and a table of items).
 *
 * Everything is manifest-driven so the dashboard is App-agnostic: branding
 * (icon/title/description/color), item nouns, the table's chip columns
 * (`layout.list` styled via `field_styles`), the status tabs (the status
 * field's enum `options`) and the lifecycle saved views
 * (`lifecycle.closing_states`) all come from the manifest. The App's `color`
 * re-themes `--accent` for the whole surface.
 *
 * Counts / filtering / topics are derived client-side from the one full-list
 * fetch (instant, exact at current scale); the server-side filter/count data
 * layer in real.ts stays available for when the list needs pagination.
 * `usePinned` / `useRecentlyViewed` back the local-only saved views.
 *
 * Metric cards show honest derived counts (open / critical-open / closed-30d) —
 * agent-run telemetry, per-resolution durations and week-over-week deltas are
 * not in the item model, so no trend arrows are fabricated.
 */

import type { CSSProperties, ReactNode } from "react";
import { useState } from "react";
import { Link, Outlet, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api } from "../api";
import type { AppItem, FieldSpec } from "../api/types";
import { summarize } from "../api/types";
import { AppIcon } from "../components/AppIcon";
import { DomainField } from "../components/DomainField";
import { HelpButton } from "../components/HelpButton";
import { Icon, type IconName } from "../components/Icon";
import { OnboardingModal } from "../components/OnboardingModal";
import { Skeleton } from "../components/Skeleton";
import { AccessChip } from "../components/AccessChip";
import { type ChipTone, chipStyle } from "../components/StatusChip";
import { UserAvatar } from "../components/UserChip";
import { useT } from "../lib/i18n";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { useIsNarrow } from "../hooks/useMediaQuery";
import { useOnboarding } from "../hooks/useOnboarding";
import { usePinned, useRecentlyViewed } from "../hooks/usePins";
import { useAppItems, useAppManifest } from "../hooks/useResources";
import { useUser, useUsers } from "../hooks/useUsers";
import { isDiscoverableOnly, itemVisibility, parseItemPermission } from "../lib/itemPermission";
import { pxToRem } from "../lib/pxToRem";

const DAY = 86_400_000;

function ago(iso?: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const days = Math.floor((Date.now() - t) / DAY);
  if (days <= 0) return "today";
  if (days === 1) return "1d ago";
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const pretty = (s: string) => cap(s.replace(/_/g, " "));
const topicsOf = (it: AppItem) => (Array.isArray(it.topics) ? it.topics.map(String) : []);

export function AppDashboard() {
  const { slug = "" } = useParams();
  const [params] = useSearchParams();
  const manifest = useAppManifest(slug);
  const { items, isPending: itemsPending } = useAppItems(slug, manifest?.resource_route);
  const { isPinned, toggle, pinned } = usePinned(slug);
  const { recent, record } = useRecentlyViewed(slug);
  const users = useUsers();
  const t = useT();
  const me = useCurrentUser();
  const meUser = useUser(me);
  const ob = useOnboarding(me, slug, manifest?.onboarding);
  const navigate = useNavigate();
  const [view, setView] = useState("all");
  const [sev, setSev] = useState("any");
  const [owner, setOwner] = useState("any");
  // A breadcrumb topic chip deep-links here as `?topic=…`; seed the filter from
  // it (one-shot, on mount) so the dashboard opens already narrowed (#158).
  const [topic, setTopic] = useState(() => params.get("topic") ?? "any");
  const [age, setAge] = useState("any");
  // Below 768px the fixed 240px sidebar can't coexist with the main column, so
  // it becomes a full-width top section and the shell stacks vertically (#464).
  const isNarrow = useIsNarrow();
  useBreadcrumbs(
    manifest ? [{ label: "Home", to: "/" }, { label: manifest.title }] : [{ label: "Home", to: "/" }],
  );

  // Loading covers two waits: the manifest, then the items list. Gate the items
  // wait on `itemsPending` too (#225) — an empty list before the first response
  // is "we don't know yet", not "no items", so falling through would flash the
  // first-user "create your first" hero (and a misleading create button). The
  // manifest is loaded by the time `itemsPending` matters, but `!manifest`
  // still shares the skeleton since the items query is disabled until then.
  if (!manifest || itemsPending) {
    return (
      <div data-testid="page-app-dashboard" style={{ padding: 28 }} aria-busy="true">
        {/* Skeleton, not bare "Loading…" (#170): a title bar + a few list rows so
            the wait reads as content arriving, not a stalled screen. */}
        <Skeleton style={{ height: 28, width: 220, marginBottom: 18 }} />
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} style={{ height: 44, marginBottom: 8, borderRadius: 8 }} />
        ))}
        {/* Keep the nested create route (`/a/:slug/new`) mounted while the
            dashboard is still loading. */}
        <Outlet />
      </div>
    );
  }

  const statusField = manifest.lifecycle?.status_field ?? "status";
  const closing = manifest.lifecycle?.closing_states ?? [];
  const list = manifest.layout.list;
  const styles = manifest.field_styles ?? {};
  // The "severity" column = the first toned, non-status field; the rest of the
  // list (minus status/severity) feeds the "topic · product" column.
  const sevField = list.find((f) => f !== statusField && styles[f]) ?? "";
  // An App only has a severity concept when a toned non-status field exists
  // (#467). Without one, "critical" is always 0 — don't surface a meaningless
  // critical counter / metric / filter (App-template driven, not slug-hardcoded).
  const hasSeverity = sevField !== "";
  const productField = list.find((f) => f !== statusField && f !== sevField) ?? "";
  const fieldSpec = (name: string): FieldSpec | undefined =>
    manifest.fields?.find((s) => s.name === name);
  const toneOf = (field: string, value: unknown): ChipTone =>
    styles[field]?.[String(value)] ?? "muted";

  const statusVal = (it: AppItem) => String(it[statusField] ?? "");
  const isClosed = (it: AppItem) => closing.includes(statusVal(it));
  const isCritical = (it: AppItem) => sevField !== "" && toneOf(sevField, it[sevField]) === "err";
  const withinDays = (it: AppItem, days: number) => {
    if (!it.updated_time) return true;
    const t = new Date(it.updated_time).getTime();
    return Number.isNaN(t) || Date.now() - t <= days * DAY;
  };

  const openItems = items.filter((it) => !isClosed(it));
  const statusOptions = fieldSpec(statusField)?.options ?? [];
  const allTopics = [...new Set(items.flatMap(topicsOf))].sort();
  // The App has a topics concept when its items carry a `topics` array (even
  // empty) — show the sidebar section then, so it's a permanent fixture like
  // the design, not something that only appears once data exists.
  const supportsTopics = items.some((it) => Array.isArray(it.topics));
  const owners = [...new Set(items.map((it) => it.owner))];
  const nameOf = (id: string) => users.find((u) => u.id === id)?.name ?? id;

  // Sidebar saved views + main status tabs both drive the same base view.
  const baseItems = (v: string): AppItem[] => {
    if (v === "pinned") return items.filter((it) => pinned.has(it.resource_id));
    if (v === "owned") return items.filter((it) => it.owner === me && !isClosed(it));
    if (v === "recent")
      return recent.map((id) => items.find((it) => it.resource_id === id)).filter((it): it is AppItem => !!it);
    if (v.startsWith("status:")) return items.filter((it) => statusVal(it) === v.slice(7));
    if (v.startsWith("life:")) return items.filter((it) => statusVal(it) === v.slice(5) && withinDays(it, 30));
    return openItems; // "all"
  };

  const secondary = (it: AppItem) =>
    (sev === "any" || String(it[sevField]) === sev) &&
    (owner === "any" || it.owner === owner) &&
    (topic === "any" || topicsOf(it).includes(topic)) &&
    (age === "any" || withinDays(it, Number(age)));

  const visible = baseItems(view).filter(secondary);

  const createLabel = manifest.item.create_label ?? `New ${manifest.item.noun}`;
  const themed = { "--accent": manifest.color } as CSSProperties;
  const noun = manifest.item.noun_plural;
  // Two kinds of empty (#161): a brand-new App with zero items (show a
  // "create your first" hero, never a zeroed "0 open · 0 critical" counter)
  // vs. filters that happen to hide everything (offer Clear filters, not a
  // dead end). The first only ever greets the first user; the second recurs.
  const isEmpty = items.length === 0;
  const clearFilters = () => {
    setView("all");
    setSev("any");
    setOwner("any");
    setTopic("any");
    setAge("any");
  };
  // The filter strip's Clear button reflects the strip selects (not the nav
  // view): enabled the moment any of them narrows the list (#172).
  const hasActiveFilter = sev !== "any" || owner !== "any" || topic !== "any" || age !== "any";

  const nav: { key: string; icon: IconName; label: string; count?: number }[] = [
    { key: "all", icon: "bug", label: "All open", count: openItems.length },
    { key: "pinned", icon: "pin", label: "Pinned", count: items.filter((it) => pinned.has(it.resource_id)).length },
    { key: "owned", icon: "eye", label: "Owned by me", count: items.filter((it) => it.owner === me && !isClosed(it)).length },
    { key: "recent", icon: "clock", label: "Recently viewed" },
    ...closing.map((s, i) => ({
      key: `life:${s}`,
      icon: (i === 0 ? "check" : "x") as IconName,
      label: `${pretty(s)} (30d)`,
      count: items.filter((it) => statusVal(it) === s && withinDays(it, 30)).length,
    })),
  ];

  const tabs = [
    { key: "all", label: "All", count: openItems.length },
    { key: "owned", label: "My open", count: items.filter((it) => it.owner === me && !isClosed(it)).length },
    ...statusOptions.map((s) => ({
      key: `status:${s}`,
      label: pretty(s),
      count: items.filter((it) => statusVal(it) === s).length,
    })),
  ];

  const openCount = openItems.length;
  const criticalCount = openItems.filter(isCritical).length;
  const closed30d = closing[0]
    ? items.filter((it) => statusVal(it) === closing[0] && withinDays(it, 30)).length
    : 0;

  return (
    <div
      data-testid="page-app-dashboard"
      style={{
        ...themed,
        display: "flex",
        flexDirection: isNarrow ? "column" : "row",
        height: "100%",
        background: "var(--paper)",
        color: "var(--text-paper)",
      }}
    >
      {/* SIDEBAR */}
      <aside
        data-testid="dash-sidebar"
        style={{
          width: isNarrow ? "100%" : 240,
          flexShrink: 0,
          borderRight: isNarrow ? "none" : "1px solid var(--paper-3)",
          borderBottom: isNarrow ? "1px solid var(--paper-3)" : "none",
          display: "flex",
          flexDirection: "column",
          padding: "18px 0",
        }}
      >
        <div style={{ padding: "0 18px 16px", borderBottom: "1px solid var(--paper-3)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <AppIcon icon={manifest.icon} color={manifest.color} size={40} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 800, fontSize: pxToRem(16), letterSpacing: "-0.02em", lineHeight: 1.1 }}>
                {manifest.title}
              </div>
              <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {summarize(manifest.description)}
              </div>
            </div>
          </div>
          <Link
            to={`/a/${slug}/new`}
            className="btn"
            data-variant="primary"
            data-size="md"
            style={{ marginTop: 16 }}
          >
            <Icon name="plus" size={14} />
            {createLabel}
          </Link>
        </div>

        <nav
          style={{
            padding: "8px 8px",
            display: "flex",
            flexDirection: isNarrow ? "row" : "column",
            gap: isNarrow ? 4 : 1,
            overflowX: isNarrow ? "auto" : "visible",
          }}
        >
          {nav.map((n) => (
            <NavRow key={n.key} icon={n.icon} label={n.label} count={n.count} active={view === n.key} onClick={() => setView(n.key)} horizontal={isNarrow} />
          ))}
        </nav>

        {supportsTopics && !isNarrow && (
          <div style={{ padding: "16px 16px 8px" }}>
            <CapsLabel>Topics</CapsLabel>
            {allTopics.length === 0 && (
              <div style={{ fontSize: pxToRem(12), color: "var(--text-paper-d2)", padding: "6px 10px" }}>
                No topics yet
              </div>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 1, marginTop: 8 }}>
              {allTopics.map((name) => {
                const count = items.filter((it) => topicsOf(it).includes(name)).length;
                return (
                  <button
                    key={name}
                    type="button"
                    onClick={() => setTopic(topic === name ? "any" : name)}
                    style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "5px 10px", border: "none", borderRadius: 4, background: topic === name ? "var(--accent-soft)" : "transparent", color: "var(--text-paper)", font: "inherit", fontSize: pxToRem(13), cursor: "pointer" }}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <Dot on={count > 0} />
                      {name}
                    </span>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: pxToRem(11), color: count > 0 ? "var(--accent)" : "var(--text-paper-d2)" }}>{count}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {!isNarrow && (
        <div style={{ marginTop: "auto", display: "flex", alignItems: "center", gap: 10, padding: "12px 14px 0", borderTop: "1px solid var(--paper-3)" }}>
          <UserAvatar userId={me} size={28} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: pxToRem(13), fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{meUser.name}</div>
            {meUser.section && (
              <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{meUser.section}</div>
            )}
          </div>
        </div>
        )}
      </aside>

      {/* MAIN */}
      <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        {isEmpty ? (
          <>
            <div style={{ display: "flex", justifyContent: "flex-end", padding: "16px 24px 0" }}>
              {manifest.onboarding && (
                <HelpButton onClick={ob.reopen} label={`About ${manifest.title}`} />
              )}
            </div>
            <div
              style={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                textAlign: "center",
                gap: 14,
                padding: 28,
              }}
            >
              <AppIcon icon={manifest.icon} color={manifest.color} />
              <h1 style={{ fontSize: pxToRem(28), fontWeight: 800, margin: 0, letterSpacing: "-0.02em" }}>
                No {noun.toLowerCase()} yet
              </h1>
              <p style={{ color: "var(--text-paper-d)", fontSize: pxToRem(14), margin: 0, maxWidth: 420 }}>
                {manifest.description || `Create your first ${manifest.item.noun.toLowerCase()} to get started.`}
              </p>
              <Link
                to={`/a/${slug}/new`}
                className="btn"
                data-variant="primary"
                data-size="md"
              >
                <Icon name="plus" size={14} color="var(--white)" />
                {createLabel}
              </Link>
            </div>
          </>
        ) : (
          <>
        {/* page header */}
        <div style={{ padding: isNarrow ? "20px 16px 16px" : "28px 28px 18px", display: "flex", flexDirection: isNarrow ? "column" : "row", alignItems: isNarrow ? "stretch" : "flex-end", justifyContent: "space-between", gap: isNarrow ? 16 : 24, borderBottom: "1px solid var(--paper-3)" }}>
          <div>
            <CapsLabel>{noun}</CapsLabel>
            <h1 style={{ fontSize: pxToRem(isNarrow ? 28 : 40), fontWeight: 800, margin: "10px 0 0", letterSpacing: "-0.02em" }}>
              {openCount} open
              {hasSeverity && (
                <>
                  {" "}
                  <span style={{ color: "var(--accent)" }}>·</span> {criticalCount} critical
                </>
              )}
            </h1>
            <p style={{ color: "var(--text-paper-d)", fontSize: pxToRem(14), margin: "8px 0 0" }}>
              All {noun.toLowerCase()} are visible to your org. Pin the ones you own.
            </p>
          </div>
          <div style={{ display: "flex", gap: isNarrow ? "12px 20px" : 28, alignItems: "flex-end", flexWrap: isNarrow ? "wrap" : "nowrap" }}>
            <Metric label="Open" value={String(openCount)} sub={noun.toLowerCase()} />
            {hasSeverity && (
              <Metric label="Critical · open" value={String(criticalCount)} sub="need attention" />
            )}
            {closing[0] && <Metric label={`${pretty(closing[0])} · 30d`} value={String(closed30d)} sub="last 30d" />}
            {manifest.onboarding && (
              <HelpButton onClick={ob.reopen} label={`About ${manifest.title}`} />
            )}
          </div>
        </div>

        {/* status tabs */}
        <div data-testid="dash-tabs" style={{ padding: isNarrow ? "0 16px" : "0 28px", borderBottom: "1px solid var(--paper-3)", display: "flex", gap: isNarrow ? 18 : 28, overflowX: isNarrow ? "auto" : "visible" }}>
          {tabs.map((t) => {
            const active = view === t.key;
            return (
              <button
                key={t.key}
                type="button"
                onClick={() => setView(t.key)}
                style={{ padding: "12px 0", flexShrink: 0, whiteSpace: "nowrap", border: "none", borderBottom: `2px solid ${active ? "var(--accent)" : "transparent"}`, background: "transparent", display: "flex", alignItems: "center", gap: 6, cursor: "pointer", font: "inherit" }}
              >
                <span style={{ fontSize: pxToRem(14), fontWeight: active ? 600 : 400, color: active ? "var(--text-paper)" : "var(--text-paper-d)" }}>{t.label}</span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: pxToRem(11), color: active ? "var(--accent)" : "var(--text-paper-d2)" }}>{t.count}</span>
              </button>
            );
          })}
        </div>

        {/* filter strip */}
        <div style={{ padding: isNarrow ? "12px 16px" : "16px 28px", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Icon name="filter" size={13} color="var(--text-paper-d)" />
          {hasSeverity && (
            <FilterSelect label="Filter by severity" prefix="Severity" value={sev} onChange={setSev} options={fieldSpec(sevField)?.options ?? []} />
          )}
          <FilterSelect label="Filter by topic" prefix="Topic" value={topic} onChange={setTopic} options={allTopics} />
          <FilterSelect label="Filter by owner" prefix="Owner" value={owner} onChange={setOwner} options={owners} labelOf={nameOf} />
          <FilterSelect label="Filter by updated" prefix="Updated" value={age} onChange={setAge} options={["7", "30"]} labelOf={(d) => `last ${d}d`} />
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            data-testid="dash-clear-filters"
            onClick={clearFilters}
            disabled={!hasActiveFilter}
          >
            {t("dash.clearFilters")}
          </button>
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
            showing {visible.length} of {items.length}
          </span>
        </div>

        {/* table */}
        <div style={{ flex: 1, overflow: "auto", padding: isNarrow ? "0 16px 16px" : "0 28px 28px" }}>
          <div role="table" data-testid="dash-items" style={{ minWidth: isNarrow ? 640 : undefined, background: "var(--white)", border: "1px solid var(--paper-3)", borderRadius: "var(--radius-card)", overflow: "hidden" }}>
            <div role="row" style={{ display: "grid", gridTemplateColumns: GRID, padding: "10px 16px", borderBottom: "1px solid var(--paper-3)", alignItems: "center", gap: 10 }}>
              <div />
              {[manifest.item.noun, manifest.labels[sevField] ?? "Severity", "Topic · product", "Owner", "Access", "Updated"].map((h) => (
                <div key={h} role="columnheader" style={{ fontSize: pxToRem(10), fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-paper-d)" }}>
                  {h}
                </div>
              ))}
            </div>

            {visible.length === 0 && (
              <div style={{ padding: 24, color: "var(--text-paper-d2)" }}>
                {/* The Clear-filters action lives in the always-visible strip
                    above (#172) — no second button down here. */}
                No {noun.toLowerCase()} match these filters.
              </div>
            )}
            {visible.map((it, i) => (
              <ItemRow
                key={it.resource_id}
                slug={slug}
                item={it}
                me={me}
                statusSpec={fieldSpec(statusField)}
                statusTone={toneOf(statusField, it[statusField])}
                sevSpec={fieldSpec(sevField)}
                sevValue={it[sevField]}
                sevTone={toneOf(sevField, it[sevField])}
                product={productField ? String(it[productField] ?? "") : ""}
                last={i === visible.length - 1}
                pinned={isPinned(it.resource_id)}
                onTogglePin={() => toggle(it.resource_id)}
                onOpen={() => record(it.resource_id)}
              />
            ))}

            <div style={{ padding: "10px 16px", background: "var(--paper-2)", borderTop: "1px solid var(--paper-3)", fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
              {visible.length === 0 ? "0" : `1–${visible.length}`} of {items.length}
            </div>
          </div>
        </div>
          </>
        )}
      </main>

      {ob.open && ob.content && (
        <OnboardingModal
          content={ob.content}
          onGotIt={ob.gotIt}
          onDontShowAgain={ob.dontShowAgain}
          onSeeFull={() => {
            ob.gotIt();
            navigate("/help");
          }}
        />
      )}

      {/* Nested create route (`/a/:slug/new`) renders here as a modal overlay. */}
      <Outlet />
    </div>
  );
}

const GRID = "32px 2.4fr 0.8fr 1.4fr 1.2fr 0.8fr 0.9fr";

function CapsLabel({ children }: { children: ReactNode }) {
  return (
    <div style={{ fontSize: pxToRem(10), fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--text-paper-d2)" }}>
      {children}
    </div>
  );
}

function Dot({ on }: { on: boolean }) {
  return (
    <span style={{ width: 6, height: 6, borderRadius: "50%", background: on ? "var(--accent)" : "var(--paper-3)", display: "inline-block" }} />
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div style={{ minWidth: 120 }}>
      <CapsLabel>{label}</CapsLabel>
      <div style={{ fontSize: pxToRem(30), fontWeight: 800, marginTop: 6, letterSpacing: "-0.02em" }}>{value}</div>
      <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d2)", marginTop: 2 }}>{sub}</div>
    </div>
  );
}

function NavRow({ icon, label, count, active, onClick, horizontal }: { icon: IconName; label: string; count?: number; active: boolean; onClick: () => void; horizontal?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-active={active ? "" : undefined}
      style={{ display: "flex", alignItems: "center", gap: 10, width: horizontal ? "auto" : "100%", flexShrink: horizontal ? 0 : undefined, height: 32, padding: "0 10px", border: "none", borderRadius: 4, background: active ? "var(--accent-soft)" : "transparent", color: active ? "var(--accent)" : "var(--text-paper-d)", font: "inherit", fontSize: pxToRem(13), cursor: "pointer", textAlign: "left" }}
    >
      <Icon name={icon} size={15} />
      <span style={{ flex: horizontal ? "0 0 auto" : 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", fontWeight: active ? 600 : 400 }}>{label}</span>
      {count != null && (
        <span style={{ fontFamily: "var(--font-mono)", fontSize: pxToRem(11), color: active ? "var(--accent)" : "var(--text-paper-d2)" }}>{count}</span>
      )}
    </button>
  );
}

function FilterSelect({
  label,
  prefix,
  value,
  onChange,
  options,
  labelOf,
}: {
  label: string;
  prefix: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  labelOf?: (v: string) => string;
}) {
  // An active (non-"any") filter is hard to spot once the <select> collapses,
  // so (a) every option carries the field prefix — a closed select reads
  // "Severity · P2", not a context-free "P2" — and (b) an active select gets an
  // accent border + text (#172).
  const active = value !== "any";
  return (
    <select
      aria-label={label}
      data-active={active ? "" : undefined}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{ height: 28, padding: "0 8px", fontSize: pxToRem(12), fontFamily: "inherit", color: active ? "var(--accent)" : "var(--text-paper)", background: "var(--white)", border: `1px solid ${active ? "var(--accent)" : "var(--paper-3)"}`, borderRadius: "var(--radius-btn)", cursor: "pointer" }}
    >
      <option value="any">{prefix} · any</option>
      {options.map((o) => (
        <option key={o} value={o}>
          {prefix} · {labelOf ? labelOf(o) : o}
        </option>
      ))}
    </select>
  );
}

// Permission-disclosure: a locked item's title — 🔒 + plain (non-link) title + a
// one-shot "request access" button that notifies the item owner (deduped BE-side).
function ItemLockedTitle({ slug, itemId, title }: { slug: string; itemId: string; title: string }) {
  const t = useT();
  const [requested, setRequested] = useState(false);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
      <span aria-hidden>🔒</span>
      <span
        title={title}
        style={{ fontWeight: 600, fontSize: pxToRem(14), color: "var(--text-paper-d)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
      >
        {title}
      </span>
      <button
        type="button"
        className="btn btn--xs"
        disabled={requested}
        onClick={() => {
          setRequested(true);
          void api.requestItemAccess(slug, itemId);
        }}
      >
        {t(requested ? "entry.withheld.requested" : "entry.withheld.requestAccess")}
      </button>
    </div>
  );
}

function ItemRow({
  slug,
  item,
  me,
  statusSpec,
  statusTone,
  sevSpec,
  sevValue,
  sevTone,
  product,
  last,
  pinned,
  onTogglePin,
  onOpen,
}: {
  slug: string;
  item: AppItem;
  me: string;
  statusSpec?: FieldSpec;
  statusTone: ChipTone;
  sevSpec?: FieldSpec;
  sevValue: unknown;
  sevTone: ChipTone;
  product: string;
  last: boolean;
  pinned: boolean;
  onTogglePin: () => void;
  onOpen: () => void;
}) {
  const owner = useUser(item.owner);
  const summary = typeof item.description === "string" ? summarize(item.description) : "";
  const topics = topicsOf(item);
  const statusVal = String(item[statusSpec?.name ?? "status"] ?? "");
  // Permission-disclosure: an item the user may see-exist (read_meta) but not enter
  // (read_chat). It stays in the list but is a locked row — no link in, a "request
  // access" action instead. Owner-for-access is `created_by`, not the `owner` field.
  const locked = isDiscoverableOnly(parseItemPermission(item.permission), me, item.created_by);

  return (
    <div role="row" style={{ display: "grid", gridTemplateColumns: GRID, padding: "14px 16px", alignItems: "center", gap: 10, borderBottom: last ? "none" : "1px solid var(--paper-3)" }}>
      <button
        type="button"
        aria-label={`${pinned ? "Unpin" : "Pin"} ${item.title}`}
        title={pinned ? "Unpin" : "Pin"}
        onClick={onTogglePin}
        disabled={locked}
        style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 24, height: 24, border: "none", background: "transparent", cursor: locked ? "default" : "pointer", color: pinned ? "var(--accent)" : "var(--text-paper-d2)", opacity: locked ? 0.4 : 1 }}
      >
        <Icon name="pin" size={14} />
      </button>

      <div style={{ minWidth: 0 }}>
        {locked ? (
          <ItemLockedTitle slug={slug} itemId={item.resource_id} title={item.title} />
        ) : (
          <Link to={`/a/${slug}/${encodeURIComponent(item.resource_id)}`} onClick={onOpen} style={{ color: "var(--text-paper)", textDecoration: "none", fontWeight: 600, fontSize: pxToRem(14) }}>
            {item.title}
          </Link>
        )}
        {summary && (
          <div style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{summary}</div>
        )}
        {statusVal && (
          <div style={{ marginTop: 6 }}>
            {statusSpec ? (
              <DomainField field={statusSpec} value={statusVal} tone={statusTone} />
            ) : (
              <span style={chipStyle(statusTone)}>{statusVal}</span>
            )}
          </div>
        )}
      </div>

      <div>
        {sevValue != null && sevValue !== "" &&
          (sevSpec ? (
            <DomainField field={sevSpec} value={sevValue} tone={sevTone} />
          ) : (
            <span style={chipStyle(sevTone)}>{String(sevValue)}</span>
          ))}
      </div>

      <div style={{ minWidth: 0, fontSize: pxToRem(13) }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, whiteSpace: "nowrap", overflow: "hidden" }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{topics[0] ?? ""}</span>
          {topics.length > 1 && (
            <span title={topics.slice(1).join(", ")} style={{ flexShrink: 0, padding: "1px 5px", border: "1px solid var(--paper-3)", borderRadius: "var(--radius-chip)", fontSize: pxToRem(10), color: "var(--text-paper-d)", fontFamily: "var(--font-mono)" }}>
              +{topics.length - 1}
            </span>
          )}
        </div>
        {product && (
          <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)", fontFamily: "var(--font-mono)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{product}</div>
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        <UserAvatar userId={item.owner} size={26} />
        <span style={{ fontSize: pxToRem(13), whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{owner.name}</span>
      </div>

      <div>
        <AccessChip visibility={itemVisibility(item.permission)} />
      </div>

      <div style={{ fontSize: pxToRem(13), color: "var(--text-paper-d)" }}>{ago(item.updated_time)}</div>
    </div>
  );
}
