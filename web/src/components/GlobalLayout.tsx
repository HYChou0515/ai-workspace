/**
 * GlobalLayout (#158) — the layout route that mounts the global nav above every
 * page and shares the breadcrumb trail between the bar (reader) and the active
 * page (publisher). The shell owns the viewport height as a flex column: the bar
 * is a fixed strip and the page fills the rest. `minHeight: 0` lets the page
 * area shrink so inner scroll containers (the IDE shell) behave instead of the
 * whole document growing past the viewport.
 */

import { Outlet } from "react-router-dom";

import { BreadcrumbProvider } from "../hooks/breadcrumbs";
import { NavChromeProvider, useNavChrome } from "../hooks/useNavChrome";
import { GlobalNav } from "./GlobalNav";
import { WriteFailureNotice } from "./WriteFailureNotice";

export function GlobalLayout() {
  return (
    <BreadcrumbProvider>
      <NavChromeProvider>
        <GlobalLayoutInner />
      </NavChromeProvider>
    </BreadcrumbProvider>
  );
}

function GlobalLayoutInner() {
  // A chat-first workspace hides the top bar (its platform overview moves into
  // the chat rail's menu) so the chat surface stays clean.
  const { hidden } = useNavChrome();
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      {!hidden && <GlobalNav />}
      {/* The page's own scroll, shared by every route — and until now the one
          place using the browser's default bar while every panel used the
          themed one, which is most of why the two look like different apps. */}
      <div className="scrollable" style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <Outlet />
      </div>
      {/* Every route nests here EXCEPT the deployed-page one (`/w/:slug/:itemId/*`,
          which is chrome-less by definition), which is why "a failed write is
          never silent" is true app-wide rather than page by page. A WUI does not
          use the mutation cache this reads — its refusals reach the page through
          the bridge's own `refuse()` — so nothing is dropped today; the day a
          mutation is added under `/w/`, it needs its own notice. Outside the
          `hidden` guard on purpose: a chat-first surface drops the nav bar as
          chrome, and this is not chrome. */}
      <WriteFailureNotice />
    </div>
  );
}
