import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { BrandIntro } from "./components/BrandIntro";
import { GlobalLayout } from "./components/GlobalLayout";
import { GlobalProgressBar } from "./components/GlobalProgressBar";
import { AppHome } from "./pages/AppHome";
import { AppNewItem } from "./pages/AppNewItem";
import { AppWorkspace } from "./pages/AppWorkspace";
import { DiagnosticsPage } from "./pages/DiagnosticsPage";
import { GroupsPage } from "./pages/GroupsPage";
import { MyResourcesPage } from "./pages/MyResourcesPage";
import { WorkCalendarPage } from "./pages/WorkCalendarPage";
import { HelpPage } from "./pages/HelpPage";
import { KbDocPage } from "./pages/kb/KbDocPage";
import { kbRoutes } from "./pages/kb/kbRoutes";
import { ReviewPage } from "./pages/kb/ReviewPage";
import { Launcher } from "./pages/Launcher";
import { WuiPage } from "./pages/WuiPage";
import { ReleasesPage } from "./pages/ReleasesPage";

/**
 * AppRoutes is router-agnostic — the host (production: <BrowserRouter>,
 * tests: <MemoryRouter>) provides the router. Multi-app routes (#89):
 *   /                          → App Launcher (pick an App)
 *   /a/:slug                   → an App's dashboard (its item list)
 *   /a/:slug/new               → the create modal, overlaid on the dashboard
 *   /a/:slug/:itemId           → an item's workspace (the generic shell)
 *   /kb                        → Knowledge base
 * Unknown paths bounce back to the launcher.
 *
 * Routes nest under <GlobalLayout> (#158) so the global nav bar + breadcrumb
 * trail render above every page and pages can publish their own crumbs — with
 * ONE exception, `/w/:slug/:itemId/*`, the deployed-page route, which is
 * chrome-less by definition. That exception costs the layout's app-wide
 * guarantees on that route; `GlobalLayout` says which and why it is safe today.
 */
export function AppRoutes() {
  return (
    <Routes>
      {/* A WUI at its own URL, OUTSIDE the shell (#WUI P17). A nav bar and a
          breadcrumb trail are for navigating a workspace; somebody who followed
          a link to one page has nowhere to navigate to and no context for the
          crumbs. Whoever opens it must already be able to see the item — the
          address is a shortcut, not a grant, and the API refuses exactly what it
          would have refused inside the workspace. */}
      <Route path="/w/:slug/:itemId/*" element={<WuiPage />} />
      <Route element={<GlobalLayout />}>
        <Route path="/" element={<Launcher />} />
        {/* `new` is a CHILD of the dashboard so the create form renders as a modal
            over the live dashboard (design-handoff), not as a standalone page. */}
        <Route path="/a/:slug" element={<AppHome />}>
          <Route path="new" element={<AppNewItem />} />
        </Route>
        <Route path="/a/:slug/:itemId" element={<AppWorkspace />} />
        {/* The KB shell + its child views (collections / a collection / chats);
            the standalone full-page doc viewer stays outside the shell. */}
        {kbRoutes()}
        <Route path="/kb/doc/*" element={<KbDocPage />} />
        <Route path="/diagnostics" element={<DiagnosticsPage />} />
        {/* #608: manage logical groups (superuser creates + designates an owner;
            owners/maintainers manage membership). Discoverable via the nav for
            superusers + anyone who belongs to a group. */}
        <Route path="/groups" element={<GroupsPage />} />
        <Route path="/work-calendar" element={<WorkCalendarPage />} />
        {/* What I am holding vs what I may hold. The limits refuse rather
            than evict, so this is where a refused person goes to free
            something up — without it, being at your limit is a dead end. */}
        <Route path="/my-resources" element={<MyResourcesPage />} />
        {/* #481: the global 審核 inbox — every pending-review item (card proposals +
            clarification questions) across every readable collection, in one
            filterable table. Absorbs the old (invisible) /clarifications page. */}
        <Route path="/review" element={<ReviewPage />} />
        <Route path="/clarifications" element={<Navigate to="/review" replace />} />
        {/* #230: the platform help / intro page (usage guides + release notes +
            an AI that answers how-to questions over the Help collection). */}
        <Route path="/help" element={<HelpPage />} />
        {/* #441: structured, per-version release notes (the /help card links here). */}
        <Route path="/help/releases" element={<ReleasesPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export function App() {
  return (
    // basename = the deploy sub-path (Vite's BASE_URL), so client routing works
    // under e.g. company.com/my-svc/rca. Defaults to "/".
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <GlobalProgressBar />
      <AppRoutes />
      <BrandIntro />
    </BrowserRouter>
  );
}
