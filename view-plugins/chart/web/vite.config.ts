/**
 * The chart plugin's web half.
 *
 * build — what `view_plugin build` runs (`vite build --outDir <dest>/chart/web`):
 * one ES module, `index.js`, with React and `@aiws/view-sdk` left EXTERNAL so
 * the host's import map hands it the host's single copies (a plugin that
 * bundles its own React takes the whole app down — plan check 3). ECharts,
 * ajv and the schema ARE bundled: they are this plugin's own.
 *
 * test — React, the testing library and the SDK resolve to the host's `web/`,
 * the same copies the import map serves at runtime.
 */
import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const host = resolve(__dirname, "../../../web");
const SHARED = ["react", "react/jsx-runtime", "react-dom", "react-dom/client", "@aiws/view-sdk"];

export default defineConfig(({ command }) => ({
  plugins: [react()],
  resolve:
    command === "build"
      ? {}
      : {
          alias: [
            { find: "@aiws/view-sdk", replacement: resolve(host, "src/renderers/entity/public.ts") },
            { find: /^react-dom(\/.*)?$/, replacement: `${resolve(host, "node_modules/react-dom")}$1` },
            { find: /^react(\/.*)?$/, replacement: `${resolve(host, "node_modules/react")}$1` },
            {
              find: /^@testing-library\/react$/,
              replacement: resolve(host, "node_modules/@testing-library/react"),
            },
          ],
        },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    lib: { entry: resolve(__dirname, "src/index.ts"), formats: ["es"], fileName: () => "index.js" },
    rollupOptions: { external: SHARED },
  },
  test: {
    environment: "happy-dom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
}));
