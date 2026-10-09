/**
 * The served page's runtime (`docs/plan-wui-multipage.md`) is put there by the
 * SERVER, which reads the file this plugin emits. Two halves, two languages —
 * so the file's name and its contents are checked here rather than trusted.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { WUI_RUNTIME_FILE, wuiRuntime } from "../vite-plugins/wuiRuntime";
import { wuiRuntimeScript } from "../src/renderers/wui/runtime";

describe("the wui-runtime build plugin", () => {
  it("emits the same runtime the single-page assembler injects", () => {
    const emitted: { type: string; fileName: string; source: string }[] = [];
    const plugin = wuiRuntime();
    const hook = plugin.generateBundle as (this: unknown, ...a: unknown[]) => void;

    hook.call({ emitFile: (f: (typeof emitted)[number]) => emitted.push(f) }, {}, {}, false);

    expect(emitted).toEqual([{ type: "asset", fileName: WUI_RUNTIME_FILE, source: wuiRuntimeScript() }]);
  });

  it("writes the file the server reads", () => {
    const py = readFileSync(resolve(__dirname, "../../src/workspace_app/api/wui_content.py"), "utf-8");

    expect(py).toContain(`RUNTIME_FILE = "${WUI_RUNTIME_FILE}"`);
  });
});
