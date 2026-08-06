/**
 * The boundary rule for `src/ext/`, enforced (#698 P3).
 *
 * A plug-in may only reach the platform through `renderers/entity/public`. That
 * is what makes the blast radius of an internal refactor visible: change
 * something the barrel exports and you can see who depends on it; change
 * anything else and `ext/` was never allowed to be looking at it.
 *
 * The plan called for an ESLint `no-restricted-imports` rule; this project has
 * no ESLint (the web toolchain is tsc + vitest), so the rule lives here — same
 * CI run, same red build.
 *
 * It does NOT use a hand-rolled scanner. Three were written and all three
 * silently stopped enforcing: a lazy regex swallowed bare `import "…"`; a
 * per-line scan could not see a multi-line import (this repo's own house
 * style); three "independent" passes shared one comment-stripper that deleted
 * real code whenever `/*` appeared inside a string. Each was a lexer written by
 * hand, and each traded one blind spot for the next. `typescript` is already a
 * dependency and ships a real one, so the scanning question is now closed.
 */
import { readFileSync, readdirSync } from "node:fs";
import { posix } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

const EXT_DIR = fileURLToPath(new URL(".", import.meta.url));

/** The single sanctioned door into the platform, relative to `ext/`. */
const PUBLIC_BARREL = "../renderers/entity/public";

/** Every `.ts`/`.tsx` under `dir`, recursively, excluding tests. Paths are
 * relative to `ext/`, which is what `violates` resolves against. */
export function sourceFiles(dir: string, base = ""): string[] {
  const out: string[] = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const rel = base ? `${base}/${e.name}` : e.name;
    if (e.isDirectory()) out.push(...sourceFiles(`${dir}/${e.name}`, rel));
    else if (/\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)) out.push(rel);
  }
  return out;
}

/** Module specifiers imported/re-exported by `text` — static, side-effect,
 * dynamic and re-export — as TypeScript's own preprocessor sees them.
 *
 * `import.meta.glob` is Vite-specific and invisible to TS, so it gets its own
 * pass: it takes a path and really does reach into the app. */
export function scanImports(text: string): string[] {
  const out = ts.preProcessFile(text, /* readImportFiles */ true, /* detectJavaScriptImports */ true).importedFiles.map(
    (f) => f.fileName,
  );
  for (const m of text.matchAll(/import\.meta\.glob\w*\s*\(\s*["'`]([^"'`]+)/g)) out.push(m[1]);
  return out;
}

/** Is this specifier, written in `ext/<fileRel>`, reaching outside the barrel?
 *
 * Resolved as a path rather than matched as a prefix. The prefix version called
 * anything starting with `./` a sibling, so `./../api/entities` walked straight
 * out; it also needed the caller to compute a `../` depth, which is the sort of
 * arithmetic that is right until someone adds a subfolder. */
export function violates(fileRel: string, spec: string): boolean {
  if (spec.startsWith("/")) return true; // Vite resolves this against the project root
  if (!spec.startsWith(".")) return false; // bare package (react, …) — fine
  const resolved = posix.normalize(posix.join(posix.dirname(fileRel), spec));
  if (!resolved.startsWith("../")) return false; // stayed inside ext/
  return resolved !== PUBLIC_BARREL;
}

describe("scanImports (TypeScript's preprocessor)", () => {
  const cases: [string, string, string[]][] = [
    ["multi-line statement — this repo's house style", 'import {\n  a,\n} from "../x";', ["../x"]],
    ["bare side-effect import before a `from` import", 'import "../a";\nimport { b } from "../c";', ["../a", "../c"]],
    ["dynamic import", 'await import("../d");', ["../d"]],
    ["dynamic import with a template literal", "await import(`../e`);", ["../e"]],
    ["re-export", 'export { a } from "../f";', ["../f"]],
    ["import.meta.glob — Vite-only, invisible to TS", 'import.meta.glob("/src/api/*.ts");', ["/src/api/*.ts"]],
    // The two traps that killed the previous hand-written comment stripper.
    ["`/*` inside a line comment", '// glob is /* like this\nimport { s } from "../g";\nconst c = "*/";', ["../g"]],
    ["`/*` inside a string", 'const g = "/*";\nimport { s } from "../h";\nconst e = "*/";', ["../h"]],
    ["a genuinely commented-out import", '// import "../i";', []],
    ["a from-quote inside JSX text is not an import", 'const N = () => <p>from "/data/x.csv"</p>;', []],
  ];
  for (const [name, text, expected] of cases) {
    it(name, () => expect(scanImports(text).sort()).toEqual([...expected].sort()));
  }
});

describe("violates() resolves the path instead of matching a prefix", () => {
  it("accepts the barrel from the top level and from a subfolder", () => {
    expect(violates("index.ts", "../renderers/entity/public")).toBe(false);
    expect(violates("acme/Deep.tsx", "../../renderers/entity/public")).toBe(false);
  });
  it("accepts siblings and bare packages", () => {
    expect(violates("index.ts", "./CsvTableView")).toBe(false);
    expect(violates("acme/Deep.tsx", "../CsvTableView")).toBe(false); // still inside ext/
    expect(violates("index.ts", "react")).toBe(false);
  });
  it("rejects reaching past the barrel however it is spelled", () => {
    expect(violates("index.ts", "../renderers/entity/shared")).toBe(true);
    expect(violates("acme/Deep.tsx", "../../api/entities")).toBe(true);
    // a `./` prefix that walks out anyway — the old prefix check called this a sibling
    expect(violates("index.ts", "./../api/entities")).toBe(true);
    expect(violates("index.ts", "./sub/../../api/entities")).toBe(true);
    // Vite root-absolute — reaches in without a single dot
    expect(violates("index.ts", "/src/api/entities")).toBe(true);
  });

  it("leaves 'that module does not exist' to tsc", () => {
    // The barrel spelled with too few `../` from a subfolder resolves INSIDE
    // ext/, to a path that isn't there. That's a compile error, not a boundary
    // breach, and tsc reports it with a better message than this rule could.
    expect(violates("acme/Deep.tsx", "../renderers/entity/public")).toBe(false);
  });
});

describe("src/ext import boundary", () => {
  it("has files to check, including any in subfolders", () => {
    expect(sourceFiles(EXT_DIR).length).toBeGreaterThan(0);
  });

  it("reaches the platform only through renderers/entity/public", () => {
    const offenders: string[] = [];
    for (const rel of sourceFiles(EXT_DIR)) {
      for (const spec of scanImports(readFileSync(EXT_DIR + rel, "utf-8"))) {
        if (violates(rel, spec)) offenders.push(`${rel} → ${spec}`);
      }
    }
    expect(offenders, "ext/ may only import the public barrel or its own siblings").toEqual([]);
  });
});
