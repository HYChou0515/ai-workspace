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
    // Every test file gets the no-network guard. See src/test/setup.ts:
    // happy-dom turns an unmocked relative fetch into a real socket, whose
    // late rejection makes vitest exit 1 with all tests passing.
    setupFiles: ["./src/test/setup.ts"],
    // The other half of that guard. `setup.ts` can only stub the fetch a TEST
    // calls; happy-dom loads a `<link rel=stylesheet>` / `<script src>` through
    // its own internal fetch, which no stub sees. A real browser never does
    // this for a document with no browsing context (what `DOMParser` builds),
    // so switching it off both matches the browser and closes the last way a
    // test can reach a socket — markup that merely NAMES a file.
    environmentOptions: {
      happyDOM: {
        settings: {
          disableCSSFileLoading: true,
          disableJavaScriptFileLoading: true,
          // NOT `disableIframePageLoading`. It would also close the `<iframe src>`
          // preview tests' sockets, but happy-dom implements `srcDoc` as a page
          // load too — so switching it off makes a self-contained frame with no
          // URL at all throw, which is a worse trade than the sockets it saves.
        },
      },
    },
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
