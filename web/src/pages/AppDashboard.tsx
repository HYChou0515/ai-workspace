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
import { Link, Outlet, useParams, useSearchParams } from "react-router-dom";

import type { AppItem, FieldSpec } from "../api/types";
import { summarize } from "../api/types";
import { AppIcon } from "../components/AppIcon";
import { DomainField } from "../components/DomainField";
import { Icon, type IconName } from "../components/Icon";
import { type ChipTone, chipStyle } from "../components/StatusChip";
import { UserAvatar } from "../components/UserChip";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { usePinned, useRecentlyViewed } from "../hooks/usePins";
import { useAppItems, useAppManifest } from "../hooks/useResources";
import { useUser, useUsers } from "../hooks/useUsers";

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
  const items = useAppItems(slug, manifest?.resource_route);
  const { isPinned, toggle, pinned } = usePinned(slug);
  const { recent, record } = useRecentlyViewed(slug);
  const users = useUsers();
  const me = useCurrentUser();
  const meUser = useUser(me);
  const [view, setView] = useState("all");
  const [sev, setSev] = useState("any");
  const [owner, setOwner] = useState("any");
  // A breadcrumb topic chip deep-links here as `?topic=…`; seed the filter from
  // it (one-shot, on mount) so the dashboard opens already narrowed (#158).
  const [topic, setTopic] = useState(() => params.get("topic") ?? "any");
  const [age, setAge] = useState("any");
  useBreadcrumbs(
    manifest ? [{ label: "Home", to: "/" }, { label: manifest.title }] : [{ label: "Home", to: "/" }],
  );

  if (!manifest) {
    return (
      <div data-testid="page-app-dashboard" style={{ padding: 28 }}>
        Loading…
        {/* Keep the nested create route (`/a/:slug/new`) mounted while the
            dashboard's manifest is still loading. */}
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
      style={{ ...themed, display: "flex", minHeight: "100%", background: "var(--paper)", color: "var(--text-paper)" }}
    >
      {/* SIDEBAR */}
      <aside
        data-testid="dash-sidebar"
        style={{ width: 240, flexShrink: 0, borderRight: "1px solid var(--paper-3)", display: "flex", flexDirection: "column", padding: "18px 0" }}
      >
        <div style={{ padding: "0 18px 16px", borderBottom: "1px solid var(--paper-3)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <AppIcon icon={manifest.icon} color={manifest.color} size={40} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 800, fontSize: 16, letterSpacing: "-0.02em", lineHeight: 1.1 }}>
                {manifest.title}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-paper-d2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {summarize(manifest.description)}
              </div>
            </div>
          </div>
          <Link
            to={`/a/${slug}/new`}
            style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, height: 36, marginTop: 16, borderRadius: "var(--radius-btn)", background: "var(--accent)", color: "var(--white)", fontSize: 13, fontWeight: 500, textDecoration: "none" }}
          >
            <Icon name="plus" size={14} />
            {createLabel}
          </Link>
        </div>

        <nav style={{ padding: "8px 8px", display: "flex", flexDirection: "column", gap: 1 }}>
          {nav.map((n) => (
            <NavRow key={n.key} icon={n.icon} label={n.label} count={n.count} active={view === n.key} onClick={() => setView(n.key)} />
          ))}
        </nav>

        {supportsTopics && (
          <div style={{ padding: "16px 16px 8px" }}>
            <CapsLabel>Topics</CapsLabel>
            {allTopics.length === 0 && (
              <div style={{ fontSize: 12, color: "var(--text-paper-d2)", padding: "6px 10px" }}>
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
                    style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "5px 10px", border: "none", borderRadius: 4, background: topic === name ? "var(--accent-soft)" : "transparent", color: "var(--text-paper)", font: "inherit", fontSize: 13, cursor: "pointer" }}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <Dot on={count > 0} />
                      {name}
                    </span>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: count > 0 ? "var(--accent)" : "var(--text-paper-d2)" }}>{count}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <div style={{ marginTop: "auto", display: "flex", alignItems: "center", gap: 10, padding: "12px 14px 0", borderTop: "1px solid var(--paper-3)" }}>
          <UserAvatar userId={me} size={28} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{meUser.name}</div>
            {meUser.section && (
              <div style={{ fontSize: 11, color: "var(--text-paper-d)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{meUser.section}</div>
            )}
          </div>
        </div>
      </aside>

      {/* MAIN */}
      <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        {/* page header */}
        <div style={{ padding: "28px 28px 18px", display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 24, borderBottom: "1px solid var(--paper-3)" }}>
          <div>
            <CapsLabel>{noun}</CapsLabel>
            <h1 style={{ fontSize: 40, fontWeight: 800, margin: "10px 0 0", letterSpacing: "-0.02em" }}>
              {openCount} open <span style={{ color: "var(--accent)" }}>·</span> {criticalCount} critical
            </h1>
            <p style={{ color: "var(--text-paper-d)", fontSize: 14, margin: "8px 0 0" }}>
              All {noun.toLowerCase()} are visible to your org. Pin the ones you own.
            </p>
          </div>
          <div style={{ display: "flex", gap: 28 }}>
            <Metric label="Open" value={String(openCount)} sub={noun.toLowerCase()} />
            <Metric label="Critical · open" value={String(criticalCount)} sub="need attention" />
            {closing[0] && <Metric label={`${pretty(closing[0])} · 30d`} value={String(closed30d)} sub="last 30d" />}
          </div>
        </div>

        {/* status tabs */}
        <div data-testid="dash-tabs" style={{ padding: "0 28px", borderBottom: "1px solid var(--paper-3)", display: "flex", gap: 28 }}>
          {tabs.map((t) => {
            const active = view === t.key;
            return (
              <button
                key={t.key}
                type="button"
                onClick={() => setView(t.key)}
                style={{ padding: "12px 0", border: "none", borderBottom: `2px solid ${active ? "var(--accent)" : "transparent"}`, background: "transparent", display: "flex", alignItems: "center", gap: 6, cursor: "pointer", font: "inherit" }}
              >
                <span style={{ fontSize: 14, fontWeight: active ? 600 : 400, color: active ? "var(--text-paper)" : "var(--text-paper-d)" }}>{t.label}</span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: active ? "var(--accent)" : "var(--text-paper-d2)" }}>{t.count}</span>
              </button>
            );
          })}
        </div>

        {/* filter strip */}
        <div style={{ padding: "16px 28px", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Icon name="filter" size={13} color="var(--text-paper-d)" />
          <FilterSelect label="Filter by severity" prefix="Severity" value={sev} onChange={setSev} options={fieldSpec(sevField)?.options ?? []} />
          <FilterSelect label="Filter by topic" prefix="Topic" value={topic} onChange={setTopic} options={allTopics} />
          <FilterSelect label="Filter by owner" prefix="Owner" value={owner} onChange={setOwner} options={owners} labelOf={nameOf} />
          <FilterSelect label="Filter by updated" prefix="Updated" value={age} onChange={setAge} options={["7", "30"]} labelOf={(d) => `last ${d}d`} />
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 12, color: "var(--text-paper-d)" }}>
            showing {visible.length} of {items.length}
          </span>
        </div>

        {/* table */}
        <div style={{ flex: 1, overflow: "auto", padding: "0 28px 28px" }}>
          <div role="table" data-testid="dash-items" style={{ background: "var(--white)", border: "1px solid var(--paper-3)", borderRadius: "var(--radius-card)", overflow: "hidden" }}>
            <div role="row" style={{ display: "grid", gridTemplateColumns: GRID, padding: "10px 16px", borderBottom: "1px solid var(--paper-3)", alignItems: "center", gap: 10 }}>
              <div />
              {[manifest.item.noun, manifest.labels[sevField] ?? "Severity", "Topic · product", "Owner", "Updated"].map((h) => (
                <div key={h} role="columnheader" style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-paper-d)" }}>
                  {h}
                </div>
              ))}
            </div>

            {visible.length === 0 && (
              <div style={{ padding: 24, color: "var(--text-paper-d2)" }}>No {noun.toLowerCase()} here.</div>
            )}
            {visible.map((it, i) => (
              <ItemRow
                key={it.resource_id}
                slug={slug}
                item={it}
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

            <div style={{ padding: "10px 16px", background: "var(--paper-2)", borderTop: "1px solid var(--paper-3)", fontSize: 12, color: "var(--text-paper-d)" }}>
              {visible.length === 0 ? "0" : `1–${visible.length}`} of {items.length}
            </div>
          </div>
        </div>
      </main>

      {/* Nested create route (`/a/:slug/new`) renders here as a modal overlay. */}
      <Outlet />
    </div>
  );
}

const GRID = "32px 2.4fr 0.8fr 1.4fr 1.2fr 0.9fr";

function CapsLabel({ children }: { children: ReactNode }) {
  return (
    <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--text-paper-d2)" }}>
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
      <div style={{ fontSize: 30, fontWeight: 800, marginTop: 6, letterSpacing: "-0.02em" }}>{value}</div>
      <div style={{ fontSize: 11, color: "var(--text-paper-d2)", marginTop: 2 }}>{sub}</div>
    </div>
  );
}

function NavRow({ icon, label, count, active, onClick }: { icon: IconName; label: string; count?: number; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-active={active ? "" : undefined}
      style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", height: 32, padding: "0 10px", border: "none", borderRadius: 4, background: active ? "var(--accent-soft)" : "transparent", color: active ? "var(--accent)" : "var(--text-paper-d)", font: "inherit", fontSize: 13, cursor: "pointer", textAlign: "left" }}
    >
      <Icon name={icon} size={15} />
      <span style={{ flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", fontWeight: active ? 600 : 400 }}>{label}</span>
      {count != null && (
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: active ? "var(--accent)" : "var(--text-paper-d2)" }}>{count}</span>
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
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{ height: 28, padding: "0 8px", fontSize: 12, fontFamily: "inherit", color: "var(--text-paper)", background: "var(--white)", border: "1px solid var(--paper-3)", borderRadius: "var(--radius-btn)", cursor: "pointer" }}
    >
      <option value="any">{prefix} · any</option>
      {options.map((o) => (
        <option key={o} value={o}>
          {labelOf ? labelOf(o) : o}
        </option>
      ))}
    </select>
  );
}

function ItemRow({
  slug,
  item,
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

  return (
    <div role="row" style={{ display: "grid", gridTemplateColumns: GRID, padding: "14px 16px", alignItems: "center", gap: 10, borderBottom: last ? "none" : "1px solid var(--paper-3)" }}>
      <button
        type="button"
        aria-label={`${pinned ? "Unpin" : "Pin"} ${item.title}`}
        title={pinned ? "Unpin" : "Pin"}
        onClick={onTogglePin}
        style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 24, height: 24, border: "none", background: "transparent", cursor: "pointer", color: pinned ? "var(--accent)" : "var(--text-paper-d2)" }}
      >
        <Icon name="pin" size={14} />
      </button>

      <div style={{ minWidth: 0 }}>
        <Link to={`/a/${slug}/${encodeURIComponent(item.resource_id)}`} onClick={onOpen} style={{ color: "var(--text-paper)", textDecoration: "none", fontWeight: 600, fontSize: 14 }}>
          {item.title}
        </Link>
        {summary && (
          <div style={{ fontSize: 12, color: "var(--text-paper-d)", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{summary}</div>
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

      <div style={{ minWidth: 0, fontSize: 13 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, whiteSpace: "nowrap", overflow: "hidden" }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{topics[0] ?? ""}</span>
          {topics.length > 1 && (
            <span title={topics.slice(1).join(", ")} style={{ flexShrink: 0, padding: "1px 5px", border: "1px solid var(--paper-3)", borderRadius: 3, fontSize: 10, color: "var(--text-paper-d)", fontFamily: "var(--font-mono)" }}>
              +{topics.length - 1}
            </span>
          )}
        </div>
        {product && (
          <div style={{ fontSize: 11, color: "var(--text-paper-d)", fontFamily: "var(--font-mono)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{product}</div>
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        <UserAvatar userId={item.owner} size={26} />
        <span style={{ fontSize: 13, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{owner.name}</span>
      </div>

      <div style={{ fontSize: 13, color: "var(--text-paper-d)" }}>{ago(item.updated_time)}</div>
    </div>
  );
}
