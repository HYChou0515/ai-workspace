/**
 * The chart plugin's web build (#847/#848): ONE ES module, `index.js`, with
 * React and the view SDK left external — the SPA's import map hands it the
 * host's copies (a plugin bundling its own React takes the whole app down,
 * plan check 3). ECharts, ajv and the spec schema ARE bundled: they are this
 * plugin's own.
 *
 * A production build, always: a dev build imports `react/jsx-dev-runtime`,
 * which the import map does not provide.
 *
 * `view-plugins/build-web.mjs` runs this with `--outDir <installed>/web`. The
 * sources and tests are type-checked and run by web/'s tsc + vitest.
 */
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  mode: "production",
  // Library mode leaves `process.env.NODE_ENV` in the output, and a browser
  // has no `process`: ECharts reads it ~200 times for its dev-only checks, so
  // the plugin threw `process is not defined` on import (found in a real
  // browser — node-based tests have a `process`, so they could not see it).
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    lib: { entry: "src/index.ts", formats: ["es"], fileName: () => "index.js" },
    rollupOptions: {
      external: ["react", "react/jsx-runtime", "react-dom", "react-dom/client", "@aiws/view-sdk"],
    },
    sourcemap: true,
  },
});
