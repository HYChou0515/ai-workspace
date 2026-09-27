// Regenerate `spec-corpus/ok-*.expect.json` from js-yaml — the parser the host
// SPA reads view files with, so it is the oracle for what a chart file MEANS.
// `src/spec.test.ts` re-checks every expect file against js-yaml, so a stale one
// fails there; the sandbox's test then holds its own YAML reader to the same
// document. Run after adding or editing an ok-* file:
//
//     node scripts/write-corpus-expect.mjs
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { load } from "js-yaml";

const corpus = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "spec-corpus");
for (const name of readdirSync(corpus).filter((n) => n.startsWith("ok-") && n.endsWith(".ai.yaml")).sort()) {
  const doc = load(readFileSync(join(corpus, name), "utf8"));
  const out = join(corpus, name.replace(/\.ai\.yaml$/, ".expect.json"));
  writeFileSync(out, JSON.stringify(doc, null, 2) + "\n");
  console.log(out);
}
