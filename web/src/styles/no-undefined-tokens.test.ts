import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve, join, relative } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Guard: every `var(--token)` referenced anywhere under src/ must resolve to a
 * custom property that is actually declared in tokens.css (or the small set of
 * runtime-assigned / third-party exceptions below). A `var(--x)` that names an
 * undefined property silently falls back — to the literal fallback if one is
 * given (often an off-brand / non-theme-aware hex), or, with no fallback, to an
 * INVALID value so the whole property drops (border/background/shadow/size
 * vanishes in BOTH themes). Both are theming bugs; this test makes them fail
 * loudly at CI instead of shipping. See issue #445.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "..");
const TOKENS_PATH = resolve(HERE, "tokens.css");

/** Custom properties that are NOT declared in tokens.css yet are legitimate. */
const RUNTIME_ASSIGNED = new Set([
  // set inline per-surface (see kb.css / FileTree) rather than in tokens.css
  "--filetree-header-bg",
]);

/** Third-party custom-property namespaces we don't own (Mantine admin UI). */
function isExternalToken(name: string): boolean {
  return name.startsWith("--mantine-");
}

function declaredTokens(): Set<string> {
  const css = readFileSync(TOKENS_PATH, "utf8");
  const names = new Set<string>();
  for (const m of css.matchAll(/^\s*(--[a-z0-9-]+)\s*:/gim)) names.add(m[1]);
  return names;
}

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const ent of readdirSync(dir, { withFileTypes: true })) {
    if (ent.name === "node_modules") continue;
    const full = join(dir, ent.name);
    if (ent.isDirectory()) {
      sourceFiles(full, out);
    } else if (/\.(tsx?|css)$/.test(ent.name) && !/\.test\.tsx?$/.test(ent.name)) {
      out.push(full);
    }
  }
  return out;
}

describe("design-token integrity (#445)", () => {
  it("references only tokens that tokens.css actually declares", () => {
    const declared = declaredTokens();
    const offenders: string[] = [];

    for (const file of sourceFiles(SRC)) {
      const text = readFileSync(file, "utf8");
      const lines = text.split("\n");
      lines.forEach((line, i) => {
        for (const m of line.matchAll(/var\(\s*(--[a-z0-9-]+)/gi)) {
          const name = m[1];
          if (declared.has(name) || RUNTIME_ASSIGNED.has(name) || isExternalToken(name)) continue;
          offenders.push(`${relative(SRC, file)}:${i + 1}  var(${name})`);
        }
      });
    }

    expect(offenders, `undefined token references:\n${offenders.join("\n")}`).toEqual([]);
  });

  /**
   * Guard: no raw white `color` literal. Text painted `#fff` on an accent /
   * status fill is invisible in neither theme *today*, but it is a hardcoded
   * hex that dodges the token system — and `--white` is the WRONG fix (it flips
   * to a dark ink in dark mode, turning the label dark-on-orange). The theme-
   * stable "light text on a coloured surface" token is `--text-dark`. See #445.
   */
  it("uses --text-dark, not a raw white hex, for text on coloured fills", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles(SRC)) {
      const text = readFileSync(file, "utf8");
      text.split("\n").forEach((line, i) => {
        if (/\bcolor:\s*["']#(?:fff|ffffff)["']/i.test(line)) {
          offenders.push(`${relative(SRC, file)}:${i + 1}  ${line.trim()}`);
        }
      });
    }
    expect(offenders, `raw white color literals:\n${offenders.join("\n")}`).toEqual([]);
  });
});
