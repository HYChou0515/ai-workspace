import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Version-skew handshake: bake the SAME version string the backend serves
// (pyproject.toml is the single source; `make release` bumps it) so the
// bundle can compare itself against the api's X-App-Version header.
const appVersion = (() => {
  try {
    const toml = readFileSync(resolve(__dirname, "../pyproject.toml"), "utf-8");
    return /^version = "([^"]+)"/m.exec(toml)?.[1] ?? "";
  } catch {
    return ""; // no pyproject in sight (isolated FE build) — skew checks disable
  }
})();

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  // Sub-path deploys (e.g. company.com/my-svc/rca): set VITE_BASE_PATH at build.
  // Bakes asset URLs + import.meta.env.BASE_URL, which the router basename and
  // the API fetch prefix both read. Default "/" (root).
  base: process.env.VITE_BASE_PATH || "/",
  plugins: [react()],
  server: {
    port: 5173,
    // #177: the whole backend lives under /api, so one proxy rule covers it and
    // every other path falls through to Vite's index.html SPA fallback — a dev
    // refresh of a client route (e.g. /kb/chats/{id}) boots the app, never JSON.
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      // Honest denominator: count every source file, even ones no test imports,
      // so the badge reflects the whole frontend — not just the tested files.
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/**/*.d.ts",
        "src/main.tsx",
        "src/test/**",
        "**/*.config.*",
      ],
    },
  },
});
