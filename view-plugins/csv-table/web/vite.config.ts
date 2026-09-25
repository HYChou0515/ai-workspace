/**
 * The csv-table plugin's web build (#847/#848): ONE ES module, `index.js`,
 * with React and the view SDK left external — the SPA's import map hands it
 * the host's copies. Bundling either would give the plugin a second React
 * (its hooks then throw) or a second registry (its kind never appears).
 *
 * A production build, always: a dev build imports `react/jsx-dev-runtime`,
 * which the import map does not provide.
 *
 * `view-plugins/build-web.mjs` runs this with `--outDir <installed>/web`.
 */
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  mode: "production",
  // Library mode leaves `process.env.NODE_ENV` in the output, and the browser
  // has no `process`: a bundled library's dev checks would throw on import.
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    lib: { entry: "src/index.tsx", formats: ["es"], fileName: () => "index.js" },
    rollupOptions: {
      external: ["react", "react/jsx-runtime", "react-dom", "react-dom/client", "@aiws/view-sdk"],
    },
    sourcemap: true,
  },
});
