#!/usr/bin/env node
/**
 * Install one runtime view plugin's WEB half and its plain files (#847/#848).
 *
 *   node view-plugins/build-web.mjs <view-plugins/NAME> <DEST_ROOT>
 *
 * → <DEST_ROOT>/NAME/{plugin.json, web/index.js (+ chunks, maps), skill/, scenarios/}
 *
 * Nothing is written into the source tree: the build goes straight to the
 * installed dir. The plugin's own `vite.config.ts` decides the shape (one ES
 * module `index.js`, React and `@aiws/view-sdk` external, production mode).
 *
 * The ONE implementation of "build a plugin's web half": the Docker
 * `view-plugins` stage runs it for every plugin, and
 * `python -m workspace_app.view_plugin build` runs it before prebuilding the
 * sandbox half — so the image and a local install cannot build it two ways.
 */
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { basename, join, resolve } from "node:path";

const [src, destRoot] = process.argv.slice(2);
if (!src || !destRoot) {
  console.error("usage: build-web.mjs <view-plugins/NAME> <DEST_ROOT>");
  process.exit(2);
}
const from = resolve(src);
const name = JSON.parse(readFileSync(join(from, "plugin.json"), "utf-8")).name;
if (name !== basename(from)) {
  console.error(`build-web: ${from}/plugin.json says name ${JSON.stringify(name)}, not its folder's`);
  process.exit(1);
}
const to = join(resolve(destRoot), name);
const web = join(from, "web");
const run = (cmd, args) => execFileSync(cmd, args, { cwd: web, stdio: "inherit" });

rmSync(join(to, "web"), { recursive: true, force: true });
mkdirSync(to, { recursive: true });
// Frozen when the plugin committed a lockfile (it must, to ship); a freshly
// scaffolded plugin has none yet, and its first install writes it.
run("pnpm", existsSync(join(web, "pnpm-lock.yaml")) ? ["install", "--frozen-lockfile"] : ["install"]);
run("pnpm", ["exec", "vite", "build", "--outDir", join(to, "web"), "--emptyOutDir"]);
if (!existsSync(join(to, "web", "index.js"))) {
  console.error(`build-web: ${name}: the build wrote no web/index.js — check its vite.config.ts lib.fileName`);
  process.exit(1);
}
cpSync(join(from, "plugin.json"), join(to, "plugin.json"));
for (const dir of ["skill", "scenarios"]) {
  rmSync(join(to, dir), { recursive: true, force: true });
  if (existsSync(join(from, dir))) cpSync(join(from, dir), join(to, dir), { recursive: true });
}
console.log(`build-web: ${name} → ${to}`);
