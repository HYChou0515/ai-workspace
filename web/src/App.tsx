import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { BrandIntro } from "./components/BrandIntro";
import { GlobalProgressBar } from "./components/GlobalProgressBar";
import { AppDashboard } from "./pages/AppDashboard";
import { AppNewItem } from "./pages/AppNewItem";
import { AppWorkspace } from "./pages/AppWorkspace";
import { DiagnosticsPage } from "./pages/DiagnosticsPage";
import { KbDocPage } from "./pages/kb/KbDocPage";
import { KbHome } from "./pages/kb/KbHome";
import { Launcher } from "./pages/Launcher";

/**
 * AppRoutes is router-agnostic — the host (production: <BrowserRouter>,
 * tests: <MemoryRouter>) provides the router. Multi-app routes (#89):
 *   /                          → App Launcher (pick an App)
 *   /a/:slug                   → an App's dashboard (its item list)
 *   /a/:slug/new               → the create modal, overlaid on the dashboard
 *   /a/:slug/:itemId           → an item's workspace (the generic shell)
 *   /kb                        → Knowledge base
 * Unknown paths bounce back to the launcher.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Launcher />} />
      {/* `new` is a CHILD of the dashboard so the create form renders as a modal
          over the live dashboard (design-handoff), not as a standalone page. */}
      <Route path="/a/:slug" element={<AppDashboard />}>
        <Route path="new" element={<AppNewItem />} />
      </Route>
      <Route path="/a/:slug/:itemId" element={<AppWorkspace />} />
      <Route path="/kb" element={<KbHome />} />
      <Route path="/kb/doc/*" element={<KbDocPage />} />
      <Route path="/diagnostics" element={<DiagnosticsPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
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
