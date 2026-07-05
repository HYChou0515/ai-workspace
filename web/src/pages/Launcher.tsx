/**
 * App Launcher (`/`) — the multi-app platform entry screen (#89).
 *
 * A neutral platform header + a gallery of App cards (each → /a/:slug) plus a
 * fixed Knowledge Base link card (→ /kb; KB is not an App). Platform chrome
 * stays neutral; each card expresses its own App `color` locally (top accent
 * bar + hover wash), per the design (direction B). The full `--accent` re-theme
 * happens after you enter an App, not here.
 */

import { Link, useNavigate } from "react-router-dom";

import { AppIcon } from "../components/AppIcon";
import { HelpButton } from "../components/HelpButton";
import { Icon } from "../components/Icon";
import { OnboardingModal } from "../components/OnboardingModal";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { useApps } from "../hooks/useResources";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { useOnboarding } from "../hooks/useOnboarding";
import { PLATFORM_ONBOARDING, PLATFORM_SCOPE } from "../lib/platformOnboarding";
import { useT } from "../lib/i18n";
import type { AppSummary } from "../api/types";
import { pxToRem } from "../lib/pxToRem";

function softOf(hex: string): string {
  return `color-mix(in srgb, ${hex} 8%, var(--white))`;
}

function AppCard({ app }: { app: AppSummary }) {
  return (
    <Link
      to={`/a/${app.slug}`}
      style={{
        display: "block",
        position: "relative",
        background: "var(--white)",
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-card)",
        overflow: "hidden",
        textDecoration: "none",
        color: "inherit",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = softOf(app.color))}
      onMouseLeave={(e) => (e.currentTarget.style.background = "var(--white)")}
    >
      <div style={{ height: 4, background: app.color }} />
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: 16 }}>
        <span
          style={{
            width: 54,
            height: 54,
            borderRadius: "var(--radius-modal)",
            background: "var(--paper-2)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <AppIcon icon={app.icon} color={app.color} />
        </span>
        <span style={{ flex: 1 }}>
          <span style={{ display: "block", fontWeight: 700, fontSize: pxToRem(18) }}>{app.title}</span>
          <span style={{ display: "block", fontSize: pxToRem(13), color: "var(--text-paper-d)" }}>
            {app.description}
          </span>
        </span>
        <Icon name="arrow_r" size={16} color="var(--text-paper-d2)" />
      </div>
    </Link>
  );
}

function KbCard() {
  const t = useT();
  return (
    <Link
      to="/kb"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: 16,
        background: "transparent",
        border: "1px dashed var(--paper-3)",
        borderRadius: "var(--radius-card)",
        textDecoration: "none",
        color: "inherit",
      }}
    >
      <span
        style={{
          width: 54,
          height: 54,
          borderRadius: "var(--radius-modal)",
          background: "var(--paper-2)",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Icon name="layers" size={24} color="var(--text-paper-d)" />
      </span>
      <span style={{ flex: 1 }}>
        <span style={{ display: "block", fontWeight: 700, fontSize: pxToRem(18) }}>
          {t("launcher.kb.title")}
        </span>
        <span style={{ display: "block", fontSize: pxToRem(13), color: "var(--text-paper-d)" }}>
          {t("launcher.kb.desc")}
        </span>
      </span>
      <Icon name="arrow_r" size={16} color="var(--text-paper-d2)" />
    </Link>
  );
}

/** #230: a fixed Help card alongside the KB card — the platform's intro / help
 * surface (usage guides + release notes + an AI that answers how-to questions). */
function HelpCard() {
  const t = useT();
  return (
    <Link
      to="/help"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: 16,
        background: "transparent",
        border: "1px dashed var(--paper-3)",
        borderRadius: "var(--radius-card)",
        textDecoration: "none",
        color: "inherit",
      }}
    >
      <span
        style={{
          width: 54,
          height: 54,
          borderRadius: "var(--radius-modal)",
          background: "var(--paper-2)",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          fontSize: pxToRem(24),
          fontWeight: 700,
          color: "var(--text-paper-d)",
        }}
      >
        ?
      </span>
      <span style={{ flex: 1 }}>
        <span style={{ display: "block", fontWeight: 700, fontSize: pxToRem(18) }}>
          {t("launcher.help.title")}
        </span>
        <span style={{ display: "block", fontSize: pxToRem(13), color: "var(--text-paper-d)" }}>
          {t("launcher.help.desc")}
        </span>
      </span>
      <Icon name="arrow_r" size={16} color="var(--text-paper-d2)" />
    </Link>
  );
}

export function Launcher() {
  const apps = useApps();
  const t = useT();
  const me = useCurrentUser();
  const ob = useOnboarding(me, PLATFORM_SCOPE, PLATFORM_ONBOARDING);
  const navigate = useNavigate();
  // The launcher is "home" — its own title bar is now redundant with the global
  // bar's brand (#158); publish a single Home crumb instead.
  useBreadcrumbs([{ label: "Home" }]);
  return (
    <div data-testid="page-launcher" style={{ minHeight: "100%", background: "var(--paper)" }}>
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
      <main style={{ maxWidth: 1080, margin: "0 auto", padding: 28 }}>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: pxToRem(11), letterSpacing: "0.12em", color: "var(--text-paper-d2)" }}>
          {t("launcher.appsEyebrow")}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "4px 0 24px" }}>
          <h1 style={{ fontSize: pxToRem(40), margin: 0 }}>{t("launcher.yourApps")}</h1>
          <HelpButton onClick={ob.reopen} label="About this workspace" />
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
            gap: 16,
          }}
        >
          {apps.length === 0 && (
            // A real empty-state with a next step (#170): apps are team/code
            // provisioned, so say so and point at the KB card just below.
            <div
              style={{
                gridColumn: "1 / -1",
                padding: "18px 20px",
                borderRadius: 12,
                border: "1px dashed var(--paper-3)",
                background: "var(--paper-2)",
              }}
            >
              <div style={{ fontSize: pxToRem(15), fontWeight: 600, marginBottom: 4 }}>
                {t("launcher.empty.title")}
              </div>
              <div style={{ fontSize: pxToRem(13), color: "var(--text-paper-d)" }}>
                {t("launcher.empty.body")}
              </div>
            </div>
          )}
          {apps.map((a) => (
            <AppCard key={a.slug} app={a} />
          ))}
          <KbCard />
          <HelpCard />
        </div>
      </main>
    </div>
  );
}
