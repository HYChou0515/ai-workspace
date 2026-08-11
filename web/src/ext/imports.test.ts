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
 * Nothing here pattern-matches source text. FOUR hand-rolled scanners were
 * written for this one rule and every one of them silently stopped enforcing:
 *
 *   1. a lazy regex that swallowed bare `import "…"` before the first `from`
 *   2. a per-line scan, blind to multi-line imports — this repo's house style
 *   3. three "independent" passes sharing one comment-stripper that deleted
 *      real code whenever `/*` appeared inside a string
 *   4. after switching the main scan to TypeScript, a leftover regex for
 *      `import.meta.glob` — which missed its documented array form (a genuine
 *      reach-in, green CI) and flagged the *string* `'try import.meta.glob(…)'`
 *
 * The fourth is the instructive one: the lesson was applied to two thirds of
 * this file and the last third kept the old habit, so the same class of bug
 * survived a whole review round. Everything now comes off TypeScript's AST,
 * including the Vite-only glob, and the file's `fileName` is passed so a `.tsx`
 * is parsed as TSX rather than TS.
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

/** Every module specifier `text` reaches for: static, side-effect, dynamic,
 * re-export, and Vite's `import.meta.glob`.
 *
 * Read off TypeScript's own AST. `fileName` matters — it selects TSX vs TS, and
 * without it a `.tsx` file is parsed as TS, where JSX text is just expressions
 * and a sentence containing `import "…"` becomes an import.
 *
 * `import.meta.glob` is Vite-only, so TypeScript doesn't model it — but it is
 * read from the SAME tree rather than a regex beside it. A regex here was the
 * fourth hand-rolled scanner in this file's history: it missed the documented
 * array form (`import.meta.glob([...])`, which really does reach in) and
 * reported the string `'try import.meta.glob("…")'` as an import. Ask the
 * parser what a call expression is; don't pattern-match source text. */
export function scanImports(text: string, fileName = "probe.tsx"): string[] {
  const kind = fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const src = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, /* setParentNodes */ true, kind);
  const out: string[] = [];
  const literal = (n: ts.Node | undefined): void => {
    if (n && ts.isStringLiteralLike(n)) out.push(n.text);
    else if (n && ts.isArrayLiteralExpression(n)) n.elements.forEach(literal);
  };

  const visit = (node: ts.Node): void => {
    // `import x from "m"` / `import "m"` / `export … from "m"` / `export * from "m"`
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
      literal(node.moduleSpecifier);
    }
    // `import x = require("m")`
    if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
      literal(node.moduleReference.expression);
    }
    if (ts.isCallExpression(node)) {
      // `import("m")` — dynamic, string or template
      if (node.expression.kind === ts.SyntaxKind.ImportKeyword) literal(node.arguments[0]);
      // `import.meta.glob("m")` / `import.meta.glob(["m", …])` / globEager
      if (
        ts.isPropertyAccessExpression(node.expression) &&
        ts.isMetaProperty(node.expression.expression) &&
        node.expression.name.text.startsWith("glob")
      ) {
        literal(node.arguments[0]);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(src);
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
    [
      "import.meta.glob's documented ARRAY form, which a regex could not see",
      'import.meta.glob(["/src/api/entities.ts", "/src/renderers/entity/shared.tsx"], { eager: true });',
      ["/src/api/entities.ts", "/src/renderers/entity/shared.tsx"],
    ],
    ["import.meta.globEager", 'import.meta.globEager("/src/api/*.ts");', ["/src/api/*.ts"]],
    ["glob named inside a STRING is not a glob", 'const help = \'try import.meta.glob("/src/api/*.ts")\';', []],
    ["glob named inside a COMMENT is not a glob", '// never import.meta.glob("/src/api/*.ts") here', []],
    // Without a .tsx filename the parser reads JSX text as expressions, and an
    // ordinary sentence in an empty state becomes an import.
    [
      "a sentence containing `import \"…\"` inside JSX text",
      'export const E = () => <p>Nothing yet — import "/data/wafer.csv" first.</p>;',
      [],
    ],
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

// The whole seam hangs off one side-effect import in the app's entry point.
// Nothing else can cover it: `CsvTableView.test.tsx` imports `./index` itself,
// so it stays green with the wiring deleted — and so does every other test,
// while in production every plug-in kind silently degrades to "Unsupported view
// kind". One line, no compiler help (it has no bindings to go unused), and the
// failure only shows in a browser.
describe("the entry point still loads ext/", () => {
  const MAIN = fileURLToPath(new URL("../main.tsx", import.meta.url));

  it("main.tsx imports ./ext", () => {
    expect(scanImports(readFileSync(MAIN, "utf-8"), "main.tsx")).toContain("./ext");
  });

  it("imports it BEFORE the first render, since the registry is a plain map", () => {
    const text = readFileSync(MAIN, "utf-8");
    const wiring = text.indexOf('import "./ext"');
    const render = text.indexOf("createRoot(");
    expect(wiring).toBeGreaterThan(-1);
    expect(render).toBeGreaterThan(-1);
    expect(wiring).toBeLessThan(render);
  });
});

describe("src/ext import boundary", () => {
  it("has files to check, including any in subfolders", () => {
    expect(sourceFiles(EXT_DIR).length).toBeGreaterThan(0);
  });

  it("reaches the platform only through renderers/entity/public", () => {
    const offenders: string[] = [];
    for (const rel of sourceFiles(EXT_DIR)) {
      for (const spec of scanImports(readFileSync(EXT_DIR + rel, "utf-8"), rel)) {
        if (violates(rel, spec)) offenders.push(`${rel} → ${spec}`);
      }
    }
    expect(offenders, "ext/ may only import the public barrel or its own siblings").toEqual([]);
  });
});
