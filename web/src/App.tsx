import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

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
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
