/**
 * Emit the WUI runtime as a file of its own (`docs/plan-wui-multipage.md`).
 *
 * A served page gets its runtime from the SERVER, which puts it at the top of
 * every page it hands out (`src/workspace_app/api/wui_content.py`). The source
 * stays where the single-page assembler and its tests already use it —
 * `runtime.ts` — and the build writes it out beside the bundle, so there is one
 * runtime, not a copy kept in step with another by hand.
 */
import { transformWithEsbuild, type Plugin } from "vite";

import { wuiRuntimeScript } from "../src/renderers/wui/runtime";

/** The file name the server reads (`RUNTIME_FILE` in `wui_content.py`). */
export const WUI_RUNTIME_FILE = "wui-runtime.js";

export function wuiRuntime(): Plugin {
  return {
    name: "aiws-wui-runtime",
    async generateBundle() {
      // Minified to ONE line: the server puts it first in each page's `<head>`,
      // and a multi-line runtime pushed every error in the page's own inline
      // script that many lines down (378, measured in review). ASCII (esbuild's
      // default), so it goes into a page of any encoding unchanged.
      const { code } = await transformWithEsbuild(wuiRuntimeScript(), WUI_RUNTIME_FILE, {
        minify: true,
        loader: "js",
        charset: "ascii",
      });
      this.emitFile({ type: "asset", fileName: WUI_RUNTIME_FILE, source: code.trim() });
    },
  };
}
