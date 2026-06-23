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
import { GlobalNav } from "./GlobalNav";

export function GlobalLayout() {
  return (
    <BreadcrumbProvider>
      <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
        <GlobalNav />
        <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          <Outlet />
        </div>
      </div>
    </BreadcrumbProvider>
  );
}
