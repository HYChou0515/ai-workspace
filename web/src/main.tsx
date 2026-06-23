import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.tsx";
import { queryClient } from "./api/queryClient";
import { initTheme } from "./hooks/theme";
import { LocaleProvider } from "./lib/i18n";
// Self-hosted fonts (Fontsource) — bundled into dist, no runtime CDN. The
// family names match the --font-* tokens in tokens.css (Inter Tight / Inter /
// JetBrains Mono), at the weights the UI actually uses.
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter-tight/700.css";
import "@fontsource/inter-tight/800.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/600.css";
import "katex/dist/katex.min.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/kb.css";
import "./styles/brand.css";
import "./styles/topic-hub.css";

initTheme();

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");
createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <LocaleProvider>
        <App />
      </LocaleProvider>
    </QueryClientProvider>
  </StrictMode>,
);
