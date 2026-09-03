import react from "@vitejs/plugin-react";

/**
 * Three settings here are load-bearing. Each failure is silent — the page
 * renders and is simply wrong or empty, with nothing in the report panel.
 */
export default {
  // 1. Vite's default emits `/assets/…`, which is workspace-root-absolute.
  //    The assembler only inlines FOLDER-relative references, so every asset
  //    goes unresolved and the page renders blank.
  base: "./",

  plugins: [react()],

  build: {
    outDir: "dist",
    rollupOptions: {
      output: {
        // 2. The assembler inlines what the ENTRY references. A code-split
        //    build leaves its lazy chunks referenced only from JS the
        //    assembler never parses — so the page loads and breaks on the
        //    first navigation instead of at build time.
        inlineDynamicImports: true,
      },
    },
  },
};

// 3. `entry: dist/index.html` in page.ai.yaml — see the comment there.
