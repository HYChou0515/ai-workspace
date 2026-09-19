// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

import { GlobalNav } from "./GlobalNav";
import { BreadcrumbProvider, useBreadcrumbs } from "../hooks/breadcrumbs";
import type { Crumb } from "../hooks/breadcrumbs";
import type { HealthApi } from "../api/health";
import { QueryWrap } from "../test/queryWrapper";

const okHealth: HealthApi = {
  getChecks: async () => ({
    running: false,
    checks: [
      {
        check_id: "c",
        description: "d",
        fast: false,
        status: "pass",
        detail: "",
        latency_ms: 1,
        checked_at: 1,
      },
    ],
  }),
  runChecks: async () => ({ started: true }),
};

vi.mock("../hooks/useResources", () => ({
  useApps: () => [
    { slug: "rca", title: "Root Cause Analysis", description: "x", icon: "flame", color: "#F0502E" },
    { slug: "yield", title: "Yield Tracking", description: "y", icon: "bug", color: "#2D6CC9" },
  ],
}));

function Pub({ crumbs }: { crumbs: Crumb[] }) {
  useBreadcrumbs(crumbs);
  return null;
}

function renderNav(path: string, crumbs: Crumb[] = [], healthClient?: HealthApi) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryWrap>
        <BreadcrumbProvider>
          <Pub crumbs={crumbs} />
          <GlobalNav healthClient={healthClient} />
        </BreadcrumbProvider>
      </QueryWrap>
    </MemoryRouter>,
  );
}

describe("GlobalNav", () => {
  it("brand links home (/)", () => {
    renderNav("/a/rca");
    expect(screen.getByRole("link", { name: /Workspace/ })).toHaveAttribute("href", "/");
  });

  it("has a persistent Help link to /help (#230)", () => {
    renderNav("/a/rca");
    expect(screen.getByRole("link", { name: "說明" })).toHaveAttribute("href", "/help");
  });

  it("brand cues it returns home — a tooltip — while still linking / (#172)", () => {
    renderNav("/a/rca");
    const brand = screen.getByRole("link", { name: /Workspace/ });
    expect(brand).toHaveAttribute("href", "/");
    expect(brand).toHaveAttribute("title", "回首頁");
  });

  it("the switcher carries a visible 切換 label, not just a bare chevron (#172)", () => {
    renderNav("/a/rca");
    const btn = screen.getByRole("button", { name: /切換/ });
    expect(btn).toHaveTextContent("切換");
  });

  it("renders the published trail: linked crumbs for `to`, plain text for the current page", () => {
    renderNav("/a/rca/123", [
      { label: "Home", to: "/" },
      { label: "RCA", to: "/a/rca" },
      { label: "Bearing noise #1432" },
    ]);
    const nav = screen.getByRole("navigation", { name: /breadcrumb/i });
    expect(within(nav).getByRole("link", { name: "RCA" })).toHaveAttribute("href", "/a/rca");
    // The current page is not a link — it's the leaf, shown as text.
    expect(within(nav).queryByRole("link", { name: "Bearing noise #1432" })).toBeNull();
    expect(within(nav).getByText("Bearing noise #1432")).toBeInTheDocument();
  });

  it("switcher dropdown jumps straight to any App, the Knowledge base, or Diagnostics", () => {
    renderNav("/a/rca");
    fireEvent.click(screen.getByRole("button", { name: /切換/ }));
    const menu = screen.getByRole("dialog");
    expect(within(menu).getByRole("link", { name: /Root Cause Analysis/ })).toHaveAttribute(
      "href",
      "/a/rca",
    );
    expect(within(menu).getByRole("link", { name: /Yield Tracking/ })).toHaveAttribute(
      "href",
      "/a/yield",
    );
    expect(within(menu).getByRole("link", { name: /Knowledge base/i })).toHaveAttribute(
      "href",
      "/kb",
    );
    expect(within(menu).getByRole("link", { name: /Diagnostics/i })).toHaveAttribute(
      "href",
      "/diagnostics",
    );
  });

  it("switcher draws every row's glyph — Apps and destinations alike", () => {
    // The look this menu and the chat rail's ☰ share is the glyph before the
    // label; the rail's test pins its side, this pins the switcher's. The href
    // cases above stay green with the glyphs deleted outright — they cannot
    // see them — so this is the only guard on the switcher's icons.
    renderNav("/a/rca");
    fireEvent.click(screen.getByRole("button", { name: /切換/ }));
    const links = within(screen.getByRole("dialog")).getAllByRole("link");
    const apps = links.filter((el) => el.getAttribute("href")?.startsWith("/a/"));
    const destinations = links.filter((el) => !el.getAttribute("href")?.startsWith("/a/"));
    expect(apps.length).toBeGreaterThan(0);
    expect(destinations.length).toBeGreaterThan(0);
    for (const el of [...apps, ...destinations]) {
      // Every row starts with NavGlyph's 22px box — whatever the glyph inside
      // (a named icon, a file, an emoji, or nothing), the box is what lines
      // the labels up, so it is what "has its glyph" means here.
      expect(el.firstElementChild, el.textContent ?? "").toHaveStyle({ width: "22px", height: "22px" });
    }
    // An App row carries the App FORM (its own manifest icon at 22px); a
    // destination row the destination form (16px). A hardcoded destination
    // glyph on an App row satisfied a bare `[data-icon]` check.
    expect(apps[0]).toHaveTextContent("Root Cause Analysis");
    expect(apps[0]!.querySelector('[data-icon="flame"]')).toHaveAttribute("width", "22");
    expect(destinations[0]!.querySelector("[data-icon]")).toHaveAttribute("width", "16");
  });

  it("switcher marks the current location (the App you're inside)", () => {
    renderNav("/a/yield/42");
    fireEvent.click(screen.getByRole("button", { name: /切換/ }));
    const menu = screen.getByRole("dialog");
    expect(within(menu).getByRole("link", { name: /Yield Tracking/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(menu).getByRole("link", { name: /Root Cause Analysis/ })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("shows the AI-health dot linking to /diagnostics", async () => {
    renderNav("/a/rca", [], okHealth);
    const dot = await screen.findByRole("link", { name: /AI features are working/i });
    expect(dot).toHaveAttribute("href", "/diagnostics");
  });

  it("exposes the platform-wide settings gear (#226)", () => {
    renderNav("/a/rca");
    expect(screen.getByRole("button", { name: "設定" })).toBeInTheDocument();
  });
});

/**
 * #fe-responsive — measured in a real browser at 390x844: the bar's own
 * children needed 417px (brand 97 + switcher 76 + Review 86 + Help 26 + health
 * + settings, plus gaps and padding). The two shrinkable regions — the
 * separator and the breadcrumb trail — were already squeezed to 0, so the
 * trailing settings button was pushed to x=406 and the whole DOCUMENT grew a
 * horizontal scrollbar. Nothing on this page is meant to scroll sideways.
 *
 * The word next to each icon is what does not fit; the icon plus its existing
 * `title` still names the destination, so narrow drops the words.
 */
describe("GlobalNav fits a narrow viewport (#fe-responsive)", () => {
  const realMM = window.matchMedia;
  afterEach(() => {
    window.matchMedia = realMM;
  });
  function stubViewport(narrow: boolean) {
    window.matchMedia = ((q: string) => ({
      matches: narrow,
      media: q,
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => true,
    })) as unknown as typeof window.matchMedia;
  }

  it("drops the word labels on narrow, keeping every destination reachable", () => {
    stubViewport(true);
    renderNav("/a/rca");
    // The words are gone…
    expect(screen.queryByText("Workspace")).not.toBeInTheDocument();
    expect(screen.queryByText("審核")).not.toBeInTheDocument();
    // …but the links, and their accessible names, are not.
    expect(screen.getByRole("link", { name: "回首頁" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "審核" })).toHaveAttribute("href", "/review");
    expect(screen.getByRole("link", { name: "說明" })).toHaveAttribute("href", "/help");
  });

  it("does not use aria-label on Review — that would hide the pending count", () => {
    // The badge is aria-hidden and the word is dropped on narrow, so `title` is
    // the ONLY place the count reaches assistive tech. An `aria-label` wins over
    // `title` in the accessible name computation and would flatten "3 件待審"
    // back to a bare "審核" — subtracting information in the one mode it would
    // have been added for.
    stubViewport(true);
    renderNav("/a/rca");
    expect(screen.getByRole("link", { name: "審核" })).not.toHaveAttribute("aria-label");
  });

  it("drops the switcher's word on narrow too — icon + chevron, name kept (plan-skill-hub-ui-polish D14)", () => {
    // Seen in the demo at 390: 「切換」 wrapped onto two lines. Same rule as
    // Brand and Review: narrow drops the word, not the control.
    stubViewport(true);
    renderNav("/a/rca");
    const btn = screen.getByRole("button", { name: "切換 App、知識庫或診斷" });
    expect(btn).not.toHaveTextContent("切換");
    expect(btn.querySelector("[data-icon]")).not.toBeNull();
  });

  it("collapses the crumbs before the last into one … on narrow, and expands them on request (D14)", () => {
    // MUI Breadcrumbs' `maxItems`: first › … › last, the ellipsis a button
    // that reveals the middle. Every crumb shrank alike before, so at 390
    // the trail read 「回…」「Skill h…」 — the current page unreadable.
    stubViewport(true);
    renderNav("/a/rca/123", [
      { label: "Home", to: "/" },
      { label: "RCA", to: "/a/rca" },
      { label: "Docs", to: "/a/rca/docs" },
      { label: "Bearing noise #1432" },
    ]);
    const nav = screen.getByRole("navigation", { name: /breadcrumb/i });
    expect(within(nav).getByText("Bearing noise #1432")).toBeInTheDocument();
    // Nothing kept before the fold — 回首頁 is the Brand's home icon, right
    // beside the trail — so the current page gets the trail's whole width.
    expect(within(nav).queryAllByRole("link")).toEqual([]);

    const more = within(nav).getByRole("button", { name: "顯示完整路徑" });
    fireEvent.click(more);

    // The folded crumbs open in a popover, NOT back into the trail: at 390
    // the bar has no room for them, so re-expanding inline re-clipped every
    // crumb (`H… › Sk… › log…`) and unmounted the focused button (review
    // round 1 of #826). The trail itself is unchanged and the button stays.
    const menu = screen.getByRole("dialog");
    expect(within(menu).getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
    expect(within(menu).getByRole("link", { name: "RCA" })).toHaveAttribute("href", "/a/rca");
    expect(within(menu).getByRole("link", { name: "Docs" })).toHaveAttribute("href", "/a/rca/docs");
    // (the popover renders inside the nav; every link in there is the menu's)
    expect(within(nav).getAllByRole("link").every((a) => menu.contains(a))).toBe(true);
    expect(within(nav).getByText("Bearing noise #1432")).toBeInTheDocument();
    expect(more).toBeInTheDocument();
    expect(more).toHaveAttribute("aria-expanded", "true");
  });

  it("leaves a two-crumb trail alone on narrow — there is nothing between first and last to fold", () => {
    stubViewport(true);
    renderNav("/skill-hub", [
      { label: "Home", to: "/" },
      { label: "Skill hub" },
    ]);
    const nav = screen.getByRole("navigation", { name: /breadcrumb/i });
    expect(within(nav).getByRole("link", { name: "Home" })).toBeInTheDocument();
    expect(within(nav).getByText("Skill hub")).toBeInTheDocument();
    expect(
      within(nav).queryByRole("button", { name: "顯示完整路徑" }),
    ).toBeNull();
  });

  it("keeps the words on a wide viewport", () => {
    stubViewport(false);
    renderNav("/a/rca", [
      { label: "Home", to: "/" },
      { label: "RCA", to: "/a/rca" },
      { label: "Docs", to: "/a/rca/docs" },
      { label: "Bearing noise #1432" },
    ]);
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.getByText("審核")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /切換/ })).toHaveTextContent(
      "切換",
    );
    // …and the whole trail, nothing folded.
    const nav = screen.getByRole("navigation", { name: /breadcrumb/i });
    expect(
      within(nav)
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual(["Home", "RCA", "Docs"]);
    expect(within(nav).queryByRole("button")).toBeNull();
  });
});
