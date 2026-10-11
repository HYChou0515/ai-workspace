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
import { PING_ANSWER } from "../src/renderers/wui/served";

describe("the wui-runtime build plugin", () => {
  it("emits the runtime the single-page assembler injects, on ONE line", async () => {
    /** The server puts it first in a page's `<head>`. Unminified it was 378
     * lines, and every error in the page's own inline script was reported 378
     * lines below where it is — the agent sent to fix the wrong line. */
    const emitted: { type: string; fileName: string; source: string }[] = [];
    const plugin = wuiRuntime();
    const hook = plugin.generateBundle as (this: unknown, ...a: unknown[]) => Promise<void>;

    await hook.call({ emitFile: (f: (typeof emitted)[number]) => emitted.push(f) }, {}, {}, false);

    expect(emitted).toHaveLength(1);
    const [file] = emitted;
    expect(file.fileName).toBe(WUI_RUNTIME_FILE);
    expect(file.source.trim().split("\n")).toHaveLength(1);
    // ASCII, so it can go into a page in any encoding unchanged.
    expect(/^[\x00-\x7f]*$/.test(file.source)).toBe(true);
    // The same runtime: the protocol, every verb, and the invocation.
    expect(file.source).toContain('"wui/1"');
    for (const verb of ["listFiles", "readFile", "writeFile", "callTool", "startRun", "openLogin"]) {
      expect(file.source).toContain(verb);
    }
    expect(wuiRuntimeScript().length).toBeGreaterThan(file.source.length);
  });

  it("writes the file the server reads", () => {
    const py = readFileSync(resolve(__dirname, "../../src/workspace_app/api/wui_content.py"), "utf-8");

    expect(py).toContain(`RUNTIME_FILE = "${WUI_RUNTIME_FILE}"`);
  });

  it("expects the ping answer the server gives — anything else sends every page the single-page way", () => {
    const py = readFileSync(resolve(__dirname, "../../src/workspace_app/api/wui_content.py"), "utf-8");

    expect(py).toContain(`PING_ANSWER = "${PING_ANSWER}"`);
  });
});
