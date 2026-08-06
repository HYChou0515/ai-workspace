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
 *
 * The first version of this file was checked against ONE violation shape and
 * therefore caught only that shape: it never recursed into subfolders, its lazy
 * `[\s\S]*?from` swallowed every bare `import "…"` before the first `from`, and
 * it could not see `import(...)` at all. `scanImports` is now tested directly
 * against each of those, so the rule cannot quietly stop enforcing again.
 */
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const EXT_DIR = fileURLToPath(new URL(".", import.meta.url));

/** The single sanctioned door into the platform. */
const PUBLIC_BARREL = "renderers/entity/public";

/** Every `.ts`/`.tsx` under `dir`, recursively, excluding tests. Relative paths.
 * Recursion matters: the guide tells authors a plug-in may span several files,
 * and a flat scan silently exempts every subfolder. */
export function sourceFiles(dir: string, base = ""): string[] {
  const out: string[] = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const rel = base ? `${base}/${e.name}` : e.name;
    if (e.isDirectory()) out.push(...sourceFiles(`${dir}/${e.name}`, rel));
    else if (/\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)) out.push(rel);
  }
  return out;
}

/** Module specifiers imported/re-exported by `text`, including side-effect and
 * dynamic imports.
 *
 * Three INDEPENDENT passes over the whole text, not one clever pattern and not a
 * per-line scan. Both earlier attempts failed the same way — each traded one
 * blind spot for another. A single lazy `import…from` pattern swallowed the bare
 * `import "…"` statements in between; scanning per line then made every
 * MULTI-LINE statement invisible, which is this repo's own house style for long
 * import lists and therefore exactly what a plug-in author copying a neighbour
 * will write. Separate anchored passes can't shadow each other. */
export function scanImports(text: string): string[] {
  const src = stripComments(text);
  const out: string[] = [];
  // `… from "m"` — covers import and re-export, however many lines it spans.
  for (const m of src.matchAll(/\bfrom\s*["']([^"']+)["']/g)) out.push(m[1]);
  // bare `import "m"` (side effects only — no `from`)
  for (const m of src.matchAll(/\bimport\s+["']([^"']+)["']/g)) out.push(m[1]);
  // dynamic `import("m")` / `import(`m`)`
  for (const m of src.matchAll(/\bimport\s*\(\s*["'`]([^"'`]+)["'`]/g)) out.push(m[1]);
  return out;
}

/** Comments only — so a commented-out import isn't reported as a violation. */
function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

/** Is this specifier allowed from a file `depth` folders below `ext/`? */
function violates(spec: string, depth: number): boolean {
  // Vite resolves a leading "/" against the project root, so `/src/api/entities`
  // reaches straight into the app without a single `.` — treat it as reaching in.
  if (spec.startsWith("/")) return true;
  if (!spec.startsWith(".")) return false; // bare package (react, …) — fine
  if (spec.startsWith("./")) return false; // sibling inside ext/ — fine
  // The barrel, reached with the right number of `../` for this file's depth.
  return spec !== `${"../".repeat(depth + 1)}${PUBLIC_BARREL}`;
}

describe("scanImports sees the shapes that used to slip past", () => {
  it("finds a bare side-effect import even when a `from` import follows it", () => {
    // The old lazy regex matched from the FIRST `import` to the FIRST `from`,
    // eating everything between — this line vanished entirely.
    const text = ['import "../hooks/useEntities";', 'import { x } from "../renderers/entity/public";'].join("\n");
    // order is not part of the contract — the scanner makes three passes
    expect(scanImports(text).sort()).toEqual(["../hooks/useEntities", "../renderers/entity/public"]);
  });

  it("finds several consecutive side-effect imports", () => {
    const text = ['import "../a.css";', 'import "../b.css";', 'import { x } from "./y";'].join("\n");
    expect(scanImports(text).sort()).toEqual(["../a.css", "../b.css", "./y"]);
  });

  it("finds a dynamic import, including mid-line", () => {
    expect(scanImports('const m = await import("../api/entities");')).toEqual(["../api/entities"]);
    expect(scanImports('const L = lazy(() => import("../renderers/csv"));')).toEqual(["../renderers/csv"]);
  });

  it("finds a re-export", () => {
    expect(scanImports('export { a } from "../secret";')).toEqual(["../secret"]);
  });

  // This repo wraps long import lists across lines — it is the house style, so
  // it is what a plug-in author copying a neighbouring file will write. The
  // per-line scanner that replaced the original regex could not see any of it.
  it("finds an import whose statement spans several lines", () => {
    const text = ["import {", "  registerViewKind,", "  useFileBuffer,", '} from "../renderers/entity/shared";'].join(
      "\n",
    );
    expect(scanImports(text)).toEqual(["../renderers/entity/shared"]);
  });

  it("finds a multi-line import even with a bare side-effect import above it", () => {
    const text = ['import "../hooks/useEntities";', "import {", "  a,", '} from "../api/entities";'].join("\n");
    expect(scanImports(text).sort()).toEqual(["../api/entities", "../hooks/useEntities"]);
  });

  it("finds a dynamic import written with a template literal", () => {
    expect(scanImports("const m = await import(`../api/entities`);")).toEqual(["../api/entities"]);
  });

  it("ignores a commented-out import rather than reporting a violation that isn't there", () => {
    expect(scanImports('// import "../api/entities";')).toEqual([]);
    expect(scanImports('/* import "../api/entities"; */')).toEqual([]);
  });
});

describe("violates() judges by the file's depth, not a fixed prefix", () => {
  it("accepts the barrel from the top level and from a subfolder", () => {
    expect(violates("../renderers/entity/public", 0)).toBe(false);
    expect(violates("../../renderers/entity/public", 1)).toBe(false);
  });
  it("rejects reaching past the barrel at any depth", () => {
    expect(violates("../renderers/entity/shared", 0)).toBe(true);
    expect(violates("../../api/entities", 1)).toBe(true);
    // ...including the barrel spelled with the WRONG depth, which wouldn't resolve
    expect(violates("../renderers/entity/public", 1)).toBe(true);
    // ...and a Vite root-absolute path, which reaches in without any dots
    expect(violates("/src/api/entities", 0)).toBe(true);
  });
  it("leaves bare packages and siblings alone", () => {
    expect(violates("react", 0)).toBe(false);
    expect(violates("./CsvTableView", 0)).toBe(false);
  });
});

describe("src/ext import boundary", () => {
  it("has files to check, including any in subfolders", () => {
    expect(sourceFiles(EXT_DIR).length).toBeGreaterThan(0);
  });

  it("reaches the platform only through renderers/entity/public", () => {
    const offenders: string[] = [];
    for (const rel of sourceFiles(EXT_DIR)) {
      const depth = rel.split("/").length - 1;
      for (const spec of scanImports(readFileSync(EXT_DIR + rel, "utf-8"))) {
        if (violates(spec, depth)) offenders.push(`${rel} → ${spec}`);
      }
    }
    expect(offenders, `ext/ may only import the public barrel or its own siblings`).toEqual([]);
  });
});
