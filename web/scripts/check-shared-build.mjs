#!/usr/bin/env node
/**
 * Built-bundle check for the shared-module import map (#847/#848 PR1 P1).
 *
 * `pnpm run build` runs this after `vite build`, so the CI build step fails
 * when the map a runtime view plugin depends on is broken:
 *
 *   - `dist/index.html` carries exactly one `<script type="importmap">`, and it
 *     comes BEFORE any module script (a map after the first module load is
 *     ignored by the browser);
 *   - the app still boots from ONE module script (a signature-preserving
 *     setting that also covers the app's own entry turns each of its imports
 *     into a script tag of its own);
 *   - every URL in it names a file that exists in `dist/`;
 *   - `shared/react.js` exports `useState` BY NAME — the CommonJS trap: a naive
 *     `export * from "react"` builds fine and exports nothing a plugin can use.
 *
 * The export list is read off TypeScript's AST, not a regex.
 *
 * Usage: node scripts/check-shared-build.mjs [distDir]   (default: ./dist)
 */
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import ts from "typescript";

const dist = resolve(process.argv[2] ?? "dist");
const base = process.env.VITE_BASE_PATH || "/";
const failures = [];

const html = readFileSync(join(dist, "index.html"), "utf-8");
const maps = [...html.matchAll(/<script type="importmap">([\s\S]*?)<\/script>/g)];
if (maps.length !== 1) {
  failures.push(`expected exactly one import map in index.html, found ${maps.length}`);
} else {
  const firstModule = html.indexOf('type="module"');
  if (firstModule !== -1 && firstModule < maps[0].index) {
    failures.push("the import map comes after a module script — the browser ignores it");
  }
  const moduleScripts = html.match(/<script type="module"/g)?.length ?? 0;
  if (moduleScripts !== 1) failures.push(`expected one module script in index.html, found ${moduleScripts}`);
  const { imports } = JSON.parse(maps[0][1]);
  for (const [spec, url] of Object.entries(imports)) {
    if (!url.startsWith(base)) {
      failures.push(`${spec} → ${url} is outside the base ${base}`);
      continue;
    }
    if (!existsSync(join(dist, url.slice(base.length)))) failures.push(`${spec} → ${url}: no such file in ${dist}`);
  }
  const reactUrl = imports.react;
  if (!reactUrl) {
    failures.push("the import map has no entry for react");
  } else {
    const file = join(dist, reactUrl.slice(base.length));
    if (existsSync(file) && !exportedNames(readFileSync(file, "utf-8")).has("useState")) {
      failures.push(`${reactUrl} does not export useState by name (the CommonJS trap)`);
    }
  }
}

/** Every name a module exports, from its AST. */
function exportedNames(text) {
  const src = ts.createSourceFile("m.js", text, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  const names = new Set();
  for (const st of src.statements) {
    if (ts.isExportDeclaration(st) && st.exportClause && ts.isNamedExports(st.exportClause)) {
      for (const el of st.exportClause.elements) names.add(el.name.text);
    }
    if (ts.isVariableStatement(st) && st.modifiers?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)) {
      for (const d of st.declarationList.declarations) if (ts.isIdentifier(d.name)) names.add(d.name.text);
    }
  }
  return names;
}

if (failures.length) {
  console.error(`check-shared-build: ${dist}\n  - ${failures.join("\n  - ")}`);
  process.exit(1);
}
console.log(`check-shared-build: ok (${dist})`);
