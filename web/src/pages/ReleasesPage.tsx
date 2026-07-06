/**
 * ReleasesPage (route /help/releases, #441) — structured, per-version release
 * notes for end users.
 *
 * Reads GET /help/releases (the packaged CHANGELOG.md that git-cliff generates,
 * parsed server-side) and renders one card per released version, newest first,
 * with a "latest" badge on the top one. A Highlights/Detailed toggle controls
 * which Keep a Changelog groups show: Highlights (the default) keeps the
 * user-facing Added/Fixed/Performance; Detailed also shows the developer-facing
 * Changed/Documentation. Unreleased sections are never shown here.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { helpApi, type HelpApi, type Release } from "../api/help";
import { qk } from "../api/queryKeys";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";

// Groups shown in the default (Highlights) view — the changes an end user cares
// about. Detailed adds the rest (Changed / Documentation).
const HIGHLIGHT_GROUPS = new Set(["Added", "Fixed", "Performance"]);

type View = "default" | "detailed";

function useGroupLabel() {
  const t = useT();
  return (group: string): string => {
    switch (group) {
      case "Added":
        return t("releases.group.added");
      case "Fixed":
        return t("releases.group.fixed");
      case "Performance":
        return t("releases.group.performance");
      case "Changed":
        return t("releases.group.changed");
      case "Documentation":
        return t("releases.group.documentation");
      default:
        return group;
    }
  };
}

function ReleaseCard({ release, latest, view }: { release: Release; latest: boolean; view: View }) {
  const t = useT();
  const groupLabel = useGroupLabel();
  const sections =
    view === "detailed" ? release.sections : release.sections.filter((s) => HIGHLIGHT_GROUPS.has(s.group));

  return (
    <section
      data-testid="release-card"
      style={{
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-card)",
        background: "var(--white)",
        padding: "16px 18px",
        marginBottom: 14,
      }}
    >
      <header style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
        <h2 data-testid="release-version" style={{ fontSize: pxToRem(17), fontWeight: 800, margin: 0 }}>
          {release.version}
        </h2>
        {latest && (
          <span
            data-testid="latest-badge"
            style={{
              fontSize: pxToRem(11),
              fontWeight: 700,
              color: "var(--white)",
              background: "var(--accent, #2563eb)",
              borderRadius: 999,
              padding: "2px 8px",
            }}
          >
            {t("releases.latest")}
          </span>
        )}
        {release.date && (
          <span style={{ color: "var(--text-paper-d)", fontSize: pxToRem(13) }}>{release.date}</span>
        )}
      </header>

      {sections.map((s) => (
        <div key={s.group} style={{ marginBottom: 10 }}>
          <h3
            style={{
              fontSize: pxToRem(12),
              fontWeight: 700,
              color: "var(--text-paper-d)",
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              margin: "0 0 4px",
            }}
          >
            {groupLabel(s.group)}
          </h3>
          <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 3 }}>
            {s.items.map((item, i) => (
              <li key={i} style={{ fontSize: pxToRem(14), lineHeight: 1.5 }}>
                {item}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

export function ReleasesPage({ client = helpApi }: { client?: HelpApi }) {
  const t = useT();
  const [view, setView] = useState<View>("default");
  useBreadcrumbs([
    { label: t("nav.home"), to: "/" },
    { label: t("help.title"), to: "/help" },
    { label: t("releases.title") },
  ]);

  const { data } = useQuery({ queryKey: qk.helpReleases, queryFn: () => client.getReleases() });
  // Never surface an Unreleased section on the public page.
  const releases = useMemo(() => (data?.releases ?? []).filter((r) => !r.unreleased), [data]);

  const toggleBtn = (v: View, labelKey: "releases.view.default" | "releases.view.detailed", testid: string) => (
    <button
      type="button"
      data-testid={testid}
      aria-pressed={view === v}
      onClick={() => setView(v)}
      style={{
        border: "1px solid var(--paper-3)",
        background: view === v ? "var(--paper-2)" : "var(--white)",
        color: "inherit",
        fontWeight: view === v ? 700 : 500,
        fontSize: pxToRem(13),
        padding: "5px 12px",
        cursor: "pointer",
      }}
    >
      {t(labelKey)}
    </button>
  );

  return (
    <div style={{ maxWidth: 820, margin: "0 auto", padding: "24px 20px", width: "100%" }}>
      <header style={{ marginBottom: 18 }}>
        <h1 style={{ fontSize: pxToRem(22), fontWeight: 800, margin: "0 0 6px" }}>{t("releases.title")}</h1>
        <p style={{ color: "var(--text-paper-d)", margin: "0 0 14px" }}>{t("releases.intro")}</p>
        <div style={{ display: "inline-flex", borderRadius: "var(--radius-card)", overflow: "hidden" }}>
          {toggleBtn("default", "releases.view.default", "view-toggle-default")}
          {toggleBtn("detailed", "releases.view.detailed", "view-toggle-detailed")}
        </div>
      </header>

      {data && releases.length === 0 && (
        <p data-testid="releases-empty" style={{ color: "var(--text-paper-d)" }}>
          {t("releases.empty")}
        </p>
      )}

      {releases.map((r, i) => (
        <ReleaseCard key={r.version} release={r} latest={i === 0} view={view} />
      ))}
    </div>
  );
}
