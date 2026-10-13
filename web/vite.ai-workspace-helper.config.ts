/**
 * Builds the card's paste converter for the helper
 * (docs/plan-outside-helper.md D11): one self-contained script, run in the
 * helper's Chrome, exposing `__aiWorkspaceHelper.aiWorkspaceHelper.htmlToMarkdown`.
 * Generated, never committed — `pnpm run build:ai-workspace-helper`, which the
 * helper's pack script and its tests run.
 */
import { resolve } from "node:path";

import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: resolve(__dirname, "src/lib/aiWorkspaceHelperBundle.ts"),
      name: "__aiWorkspaceHelper",
      formats: ["iife"],
      fileName: () => "page_to_markdown.js",
    },
    outDir: resolve(__dirname, "../ai-workspace-helper/src/ai_workspace_helper"),
    // the package's own source lives there
    emptyOutDir: false,
    copyPublicDir: false,
    minify: true,
  },
});
