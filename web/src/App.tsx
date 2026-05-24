import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { BrandIntro } from "./components/BrandIntro";
import { Home } from "./pages/Home";
import { Investigation } from "./pages/Investigation";
import { KbDocPage } from "./pages/kb/KbDocPage";
import { KbHome } from "./pages/kb/KbHome";

/**
 * AppRoutes is router-agnostic — the host (production: <BrowserRouter>,
 * tests: <MemoryRouter>) provides the router. Routes per plan §4:
 *   /                          → Home (investigation list)
 *   /investigations/:id        → Investigation (workspace)
 *   /kb                        → Knowledge base (collections, chats, ask agent)
 * Unknown paths bounce back to Home.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/investigations/:id" element={<Investigation />} />
      <Route path="/kb" element={<KbHome />} />
      <Route path="/kb/doc/*" element={<KbDocPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    // basename = the deploy sub-path (Vite's BASE_URL), so client routing works
    // under e.g. company.com/my-svc/rca. Defaults to "/".
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <AppRoutes />
      <BrandIntro />
    </BrowserRouter>
  );
}
