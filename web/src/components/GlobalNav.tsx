/**
 * Global navigation bar (#158) — one fixed strip rendered above every page by
 * the `GlobalLayout` layout route. It gives the whole platform a "home" and
 * signposts: a brand that returns to the launcher, a switcher to jump straight
 * to any App / the Knowledge base / Diagnostics (no more backtracking to `/`),
 * and the current breadcrumb trail published by the active page.
 */

import { Fragment, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import type { HealthApi } from "../api/health";
import { useT } from "../lib/i18n";
import { useBreadcrumbTrail } from "../hooks/breadcrumbs";
import { useApps } from "../hooks/useResources";
import { AppIcon } from "./AppIcon";
import { GlobalSettings } from "./GlobalSettings";
import { HealthDot } from "./HealthDot";
import { Icon } from "./Icon";
import type { IconName } from "./Icon";
import { Popover } from "./Popover";

/** A destination is "current" when the path is it or nested under it. */
function isActive(pathname: string, to: string): boolean {
  return pathname === to || pathname.startsWith(`${to}/`);
}

function MenuLink({
  to,
  active,
  children,
}: {
  to: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      to={to}
      aria-current={active ? "page" : undefined}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "7px 12px",
        textDecoration: "none",
        color: "var(--text-paper)",
        fontSize: "var(--text-body-sm)",
        fontWeight: active ? 700 : 500,
        background: active ? "var(--paper-2)" : "transparent",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </Link>
  );
}

function FixedLink({ to, icon, label, pathname }: { to: string; icon: IconName; label: string; pathname: string }) {
  return (
    <MenuLink to={to} active={isActive(pathname, to)}>
      <span
        style={{
          width: 22,
          height: 22,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Icon name={icon} size={16} color="var(--text-paper-d)" />
      </span>
      {label}
    </MenuLink>
  );
}

function Switcher() {
  const apps = useApps();
  const { pathname } = useLocation();
  const t = useT();
  return (
    <Popover
      align="start"
      width={260}
      trigger={({ onClick, open }) => (
        <button
          type="button"
          aria-label={t("nav.switch.tip")}
          title={t("nav.switch.tip")}
          aria-expanded={open}
          onClick={onClick}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 3,
            color: "var(--text-paper-d)",
            padding: "2px 8px",
            fontSize: "var(--text-body-sm)",
            border: "1px solid var(--paper-3)",
            borderRadius: "var(--radius-btn)",
            background: "var(--white)",
            cursor: "pointer",
          }}
        >
          <span>{t("nav.switch")}</span>
          <Icon name="chev_d" size={14} />
        </button>
      )}
    >
      {(close) => (
        <div onClick={close} style={{ padding: "6px 0" }}>
          {apps.map((app) => (
            <MenuLink key={app.slug} to={`/a/${app.slug}`} active={isActive(pathname, `/a/${app.slug}`)}>
              <AppIcon icon={app.icon} color={app.color} size={22} />
              {app.title}
            </MenuLink>
          ))}
          <div style={{ height: 1, background: "var(--paper-3)", margin: "6px 0" }} />
          <FixedLink to="/kb" icon="layers" label="Knowledge base" pathname={pathname} />
          <FixedLink to="/diagnostics" icon="sparkle" label="Diagnostics" pathname={pathname} />
        </div>
      )}
    </Popover>
  );
}

function Breadcrumbs() {
  const trail = useBreadcrumbTrail();
  if (trail.length === 0) return null;
  return (
    <nav
      aria-label="Breadcrumb"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        minWidth: 0,
        fontSize: "var(--text-body-sm)",
        color: "var(--text-paper-d)",
      }}
    >
      {trail.map((crumb, i) => {
        const last = i === trail.length - 1;
        return (
          <Fragment key={`${crumb.label}-${i}`}>
            {i > 0 && <Icon name="chev_r" size={12} color="var(--text-paper-d2)" />}
            {crumb.to && !last ? (
              <Link
                to={crumb.to}
                style={{
                  color: "var(--text-paper-d)",
                  textDecoration: "none",
                  whiteSpace: "nowrap",
                }}
              >
                {crumb.label}
              </Link>
            ) : (
              <span
                style={{
                  color: "var(--text-paper)",
                  fontWeight: last ? 600 : 400,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {crumb.label}
              </span>
            )}
          </Fragment>
        );
      })}
    </nav>
  );
}

/** The product wordmark, doubling as a "return home" link. A bare bold word
 * didn't read as clickable (#172) — pair it with a home icon, a tooltip, and a
 * hover underline so the affordance is obvious. */
function Brand() {
  const t = useT();
  const [hover, setHover] = useState(false);
  return (
    <Link
      to="/"
      title={t("nav.home")}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontWeight: 800,
        color: "var(--text-paper)",
        textDecoration: hover ? "underline" : "none",
      }}
    >
      <Icon name="home" size={15} color="var(--text-paper-d)" />
      Workspace
    </Link>
  );
}

export function GlobalNav({ healthClient }: { healthClient?: HealthApi }) {
  return (
    <header
      style={{
        height: 40,
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "0 12px",
        background: "var(--white)",
        borderBottom: "1px solid var(--paper-3)",
      }}
    >
      <Brand />
      <Switcher />
      <span style={{ width: 1, height: 20, background: "var(--paper-3)" }} />
      <Breadcrumbs />
      <span style={{ flex: 1 }} />
      <HealthDot {...(healthClient ? { client: healthClient } : {})} />
      <GlobalSettings />
    </header>
  );
}
