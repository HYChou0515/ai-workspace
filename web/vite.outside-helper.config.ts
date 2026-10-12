/**
 * Builds the card's paste converter for the outside helper
 * (docs/plan-outside-helper.md D11): one self-contained script, run in the
 * helper's Chrome, exposing `__outsideHelper.outsideHelper.htmlToMarkdown`.
 * Generated, never committed — `pnpm run build:outside-helper`, which the
 * helper's pack script and its tests run.
 */
import { resolve } from "node:path";

import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: resolve(__dirname, "src/lib/outsideHelperBundle.ts"),
      name: "__outsideHelper",
      formats: ["iife"],
      fileName: () => "page_to_markdown.js",
    },
    outDir: resolve(__dirname, "../outside-helper/src/outside_helper"),
    // the package's own source lives there
    emptyOutDir: false,
    copyPublicDir: false,
    minify: true,
  },
});
