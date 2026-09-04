import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.tsx";
import { queryClient } from "./api/queryClient";
import { DialogProvider } from "./components/Dialog";
import { ToolCatalogProvider } from "./components/toolCatalog";
import { FontScaleProvider, initFontScale } from "./hooks/fontScale";
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
import "./styles/entity-views.css";
import "./styles/chat-rail.css";
import "./styles/sheet.css";
import "./styles/my-resources.css";
import "./styles/item-environment.css";
// #698 — second-party view kinds register themselves on import. This runs
// before the first render below, which it must: the registry is a plain map, so
// a kind added after a view has painted would not appear in it.
import "./ext";

initTheme();
initFontScale();

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");
createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <LocaleProvider>
        <FontScaleProvider>
          <ToolCatalogProvider>
            {/* #779: the confirm dialog belongs at the root, not per-surface.
                It used to be mounted in five places, so a modal under
                components/ could not reach useDialog() and had to hand-roll
                its own "discard unsaved changes?" row instead. */}
            <DialogProvider>
              <App />
            </DialogProvider>
          </ToolCatalogProvider>
        </FontScaleProvider>
      </LocaleProvider>
    </QueryClientProvider>
  </StrictMode>,
);
