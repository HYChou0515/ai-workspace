import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Home } from "./pages/Home";
import { Investigation } from "./pages/Investigation";

/**
 * AppRoutes is router-agnostic — the host (production: <BrowserRouter>,
 * tests: <MemoryRouter>) provides the router. Two routes per plan §4:
 *   /                          → Home (investigation list)
 *   /investigations/:id        → Investigation (workspace)
 * Unknown paths bounce back to Home.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/investigations/:id" element={<Investigation />} />
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
