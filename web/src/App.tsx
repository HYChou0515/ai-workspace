import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { BrandIntro } from "./components/BrandIntro";
import { GlobalLayout } from "./components/GlobalLayout";
import { GlobalProgressBar } from "./components/GlobalProgressBar";
import { AppDashboard } from "./pages/AppDashboard";
import { AppNewItem } from "./pages/AppNewItem";
import { AppWorkspace } from "./pages/AppWorkspace";
import { DiagnosticsPage } from "./pages/DiagnosticsPage";
import { GroupsPage } from "./pages/GroupsPage";
import { HelpPage } from "./pages/HelpPage";
import { KbDocPage } from "./pages/kb/KbDocPage";
import { kbRoutes } from "./pages/kb/kbRoutes";
import { GraphBrowsePage } from "./pages/kb/GraphBrowsePage";
import { GraphEntityPage } from "./pages/kb/GraphEntityPage";
import { ReviewPage } from "./pages/kb/ReviewPage";
import { Launcher } from "./pages/Launcher";
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
 * All routes nest under <GlobalLayout> (#158) so the global nav bar + breadcrumb
 * trail render above every page and pages can publish their own crumbs.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<GlobalLayout />}>
        <Route path="/" element={<Launcher />} />
        {/* `new` is a CHILD of the dashboard so the create form renders as a modal
            over the live dashboard (design-handoff), not as a standalone page. */}
        <Route path="/a/:slug" element={<AppDashboard />}>
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
        {/* #481: the global 審核 inbox — every pending-review item (card proposals +
            clarification questions) across every readable collection, in one
            filterable table. Absorbs the old (invisible) /clarifications page. */}
        <Route path="/review" element={<ReviewPage />} />
        {/* #636: the graph browser — the only entry that does not require
            already knowing an entity's id. */}
        <Route path="/kb/graph" element={<GraphBrowsePage />} />
        <Route path="/kb/graph/entities/:entityId" element={<GraphEntityPage />} />
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
