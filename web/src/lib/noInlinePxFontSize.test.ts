import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * Guard (#226): inline pixel font sizes bypass the rem-based system-font-size
 * scale, so a raw `fontSize: <number>` in a component would not grow with the
 * user's setting. Every UI font size must go through `pxToRem(n)` (or a
 * --text-* token). This test fails listing any offender so the scaling can't
 * silently regress.
 *
 * Monaco editor `fontSize` options are real px numbers (not CSS rem), but they
 * go through useMonacoFontSize(base) so they too scale with the setting — so
 * even they are no longer bare numeric literals. The allowlist is empty.
 */
const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");

const ALLOWLIST: string[] = [];

function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules") continue;
      out.push(...tsxFiles(full));
    } else if (entry.name.endsWith(".tsx") && !entry.name.endsWith(".test.tsx")) {
      out.push(full);
    }
  }
  return out;
}

describe("no raw inline px font sizes", () => {
  it("every fontSize goes through pxToRem (or a token), not a bare number", () => {
    const offenders: string[] = [];
    for (const file of tsxFiles(SRC)) {
      const rel = relative(SRC, file).replaceAll("\\", "/");
      if (ALLOWLIST.includes(rel)) continue;
      const lines = readFileSync(file, "utf8").split("\n");
      lines.forEach((line, i) => {
        if (/fontSize:\s*\d/.test(line)) offenders.push(`${rel}:${i + 1}`);
      });
    }
    expect(offenders).toEqual([]);
  });
});
