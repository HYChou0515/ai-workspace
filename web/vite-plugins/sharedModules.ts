/**
 * The shared-module build and its import map (#847/#848 PR1 P1).
 *
 * A runtime view plugin is a separately built ES module that leaves `react`
 * (and the view SDK) EXTERNAL: its `import { useState } from "react"` must reach
 * the HOST's single copy, or its hooks run against a React that never rendered
 * anything and throw `Cannot read properties of null (reading 'useState')` —
 * which, in the planning spike, took the whole host tree down with it.
 *
 * The browser resolves a bare specifier through an import map, so this plugin:
 *
 *   1. emits one ESM FACADE per shared package as an extra rollup entry, at a
 *      fixed unhashed name `shared/<name>.js`. React ships CommonJS, so
 *      `export * from "react"` would export nothing by name; each facade lists
 *      every key explicitly. `preserveEntrySignatures: "exports-only"` stops
 *      rollup from tree-shaking those exports away (nothing in the host imports
 *      them). Not `"strict"`: that also forces a facade chunk for the app's own
 *      export-less entry, and Vite then emits every one of its imports as a
 *      separate `<script type="module">` instead of one entry plus preloads.
 *   2. prepends `<script type="importmap">` to index.html, pointing each shared
 *      specifier at its facade. Under the dev server the facades are the
 *      virtual modules as Vite serves them (`/@id/__x00__shared:<spec>`), whose
 *      own `import … from "react"` Vite rewrites to the same pre-bundled URL the
 *      host imports — still one React. Never hardcode a `.vite/deps` URL: its
 *      `?v=` hash changes on every re-optimise.
 *
 * Because the names are fixed, `shared/*.js` must be revalidated on every load
 * (`api/spa.py` serves them `no-cache`), or a rebuild would pair a stale facade
 * with new hashed chunks.
 *
 * If a `script-src` CSP is ever added to the SPA document, it must carry this
 * inline map's hash or a nonce — without it the map is blocked and every plugin
 * fails with "Failed to resolve module specifier".
 */
import { createRequire } from "node:module";
import { resolve } from "node:path";

import type { Plugin } from "vite";

/** Specifier → the file name under `shared/`. Adding a package here adds it to
 * the build, the map, and `check-shared-build.mjs` at once. */
export const SHARED_MODULES: Record<string, string> = {
  react: "react",
  "react/jsx-runtime": "react-jsx-runtime",
  "react-dom": "react-dom",
  "react-dom/client": "react-dom-client",
};

const VIRTUAL_PREFIX = "shared:";
const IDENT = /^[A-Za-z_$][\w$]*$/;

const requireFromWeb = createRequire(resolve(__dirname, "..", "package.json"));

/** The names `spec` exports, read off the installed package itself — so a React
 * upgrade that adds a hook needs no edit here. */
export function sharedExportNames(spec: string): string[] {
  const mod = requireFromWeb(spec) as Record<string, unknown>;
  return Object.keys(mod)
    .filter((k) => k !== "default" && k !== "__esModule" && IDENT.test(k))
    .sort();
}

/** An ES module that re-exports a CommonJS package key by key. */
export function facadeSource(spec: string, names: string[]): string {
  const lines = [`import __m from ${JSON.stringify(spec)};`, "export default __m;"];
  for (const n of names) lines.push(`export const ${n} = __m.${n};`);
  return lines.join("\n") + "\n";
}

/** The import map for `command` ("serve" = dev server, "build" = dist). */
export function importMap(command: "serve" | "build", base: string): { imports: Record<string, string> } {
  const root = base.endsWith("/") ? base : `${base}/`;
  const imports: Record<string, string> = {};
  for (const [spec, file] of Object.entries(SHARED_MODULES)) {
    imports[spec] = command === "serve" ? `${root}@id/__x00__${VIRTUAL_PREFIX}${spec}` : `${root}shared/${file}.js`;
  }
  return { imports };
}

/** Rollup inputs for the facades, keyed by output name (`shared/<file>`). */
export function sharedInputs(): Record<string, string> {
  return Object.fromEntries(Object.entries(SHARED_MODULES).map(([spec, file]) => [`shared/${file}`, VIRTUAL_PREFIX + spec]));
}

export function sharedModules(): Plugin {
  let command: "serve" | "build" = "serve";
  let base = "/";
  return {
    name: "aiws-shared-modules",
    config(_cfg, env) {
      if (env.command !== "build") return;
      return {
        build: {
          rollupOptions: {
            input: { index: resolve(__dirname, "..", "index.html"), ...sharedInputs() },
            preserveEntrySignatures: "exports-only",
            output: {
              entryFileNames: (chunk) => (chunk.name.startsWith("shared/") ? "[name].js" : "assets/[name]-[hash].js"),
            },
          },
        },
      };
    },
    configResolved(cfg) {
      command = cfg.command;
      base = cfg.base;
    },
    resolveId(id) {
      if (id.startsWith(VIRTUAL_PREFIX) && id.slice(VIRTUAL_PREFIX.length) in SHARED_MODULES) return `\0${id}`;
      return null;
    },
    load(id) {
      if (!id.startsWith(`\0${VIRTUAL_PREFIX}`)) return null;
      const spec = id.slice(VIRTUAL_PREFIX.length + 1);
      return facadeSource(spec, sharedExportNames(spec));
    },
    transformIndexHtml: {
      order: "post",
      handler() {
        return [
          {
            tag: "script",
            attrs: { type: "importmap" },
            children: JSON.stringify(importMap(command, base)),
            injectTo: "head-prepend",
          },
        ];
      },
    },
  };
}
