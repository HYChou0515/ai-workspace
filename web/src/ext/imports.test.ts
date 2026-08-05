/**
 * The boundary rule for `src/ext/`, enforced (#698 P3).
 *
 * A plug-in may only reach the platform through `renderers/entity/public`. That
 * is what makes the blast radius of an internal refactor visible: change
 * something the barrel exports and you can see who depends on it; change
 * anything else and `ext/` was never allowed to be looking at it.
 *
 * The plan called for an ESLint `no-restricted-imports` rule, but this project
 * has no ESLint (the web toolchain is tsc + vitest). Rather than pull in a
 * linter for one rule, the rule lives here — same CI run, same red build.
 */
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const EXT_DIR = fileURLToPath(new URL(".", import.meta.url));

/** The single sanctioned door into the platform. */
const PUBLIC_BARREL = "../renderers/entity/public";

function sourceFiles(): string[] {
  return readdirSync(EXT_DIR).filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f));
}

/** Every module specifier in `import ... from "x"` / `import "x"` / re-exports. */
function importSpecifiers(text: string): string[] {
  const out: string[] = [];
  const re = /(?:^|\n)\s*(?:import|export)[\s\S]*?from\s*["']([^"']+)["']|(?:^|\n)\s*import\s*["']([^"']+)["']/g;
  for (const m of text.matchAll(re)) out.push(m[1] ?? m[2]);
  return out;
}

describe("src/ext import boundary", () => {
  it("has files to check (so this test can't pass by finding nothing)", () => {
    expect(sourceFiles().length).toBeGreaterThan(0);
  });

  it("reaches the platform only through renderers/entity/public", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles()) {
      for (const spec of importSpecifiers(readFileSync(EXT_DIR + file, "utf-8"))) {
        // Bare package imports (react, …) are fine — the rule is about reaching
        // INTO this app's internals.
        if (!spec.startsWith(".")) continue;
        // A sibling inside ext/ is fine; a plug-in is allowed several files.
        if (spec.startsWith("./")) continue;
        if (spec === PUBLIC_BARREL) continue;
        offenders.push(`${file} → ${spec}`);
      }
    }
    expect(offenders, `ext/ may only import "${PUBLIC_BARREL}" or its own siblings`).toEqual([]);
  });

  it("actually parses the imports it is checking", () => {
    // Guards the regex above: if it silently matched nothing, the rule would
    // pass vacuously for every file.
    const index = readFileSync(EXT_DIR + "index.ts", "utf-8");
    expect(importSpecifiers(index)).toContain(PUBLIC_BARREL);
  });
});
