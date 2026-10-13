/**
 * Builds the card's paste converter for the helper
 * (docs/plan-outside-helper.md D11): one self-contained script, run in the
 * helper's Chrome, exposing `__aiWorkspace.aiWorkspace.htmlToMarkdown`.
 * Generated, never committed — `pnpm run build:ai-workspace`, which the
 * helper's pack script and its tests run.
 */
import { resolve } from "node:path";

import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: resolve(__dirname, "src/lib/aiWorkspaceBundle.ts"),
      name: "__aiWorkspace",
      formats: ["iife"],
      fileName: () => "page_to_markdown.js",
    },
    outDir: resolve(__dirname, "../ai-workspace/src/ai_workspace"),
    // the package's own source lives there
    emptyOutDir: false,
    copyPublicDir: false,
    minify: true,
  },
});
