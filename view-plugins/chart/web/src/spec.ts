/**
 * The renderer's reader for a `view: chart` document.
 *
 * The schema is ONE file, owned by the sandbox half (`chart_view/spec.schema.json`)
 * and imported here as-is, so the sandbox's `validate` and this renderer cannot
 * disagree about what a chart file may say. `spec.test.ts` runs the shared corpus
 * through both. `errorMessage` in the schema is the ajv-errors keyword; the
 * sandbox reads the same sentences.
 */
import Ajv2020, { type ErrorObject } from "ajv/dist/2020";
import ajvErrors from "ajv-errors";

import schema from "../../sandbox-src/src/chart_view/spec.schema.json";

const ajv = new Ajv2020({ allErrors: true, strict: true, strictTypes: false, strictRequired: false });
ajvErrors(ajv);
const validate = ajv.compile(schema);

/** The choices ajv reports alongside the errors inside them; the inner ones say more. */
const CHOICE = new Set(["oneOf", "anyOf", "if"]);

function where(e: ErrorObject): string {
  const parts = e.instancePath.split("/").slice(1);
  const path = parts.map((p) => (/^\d+$/.test(p) ? `[${p}]` : `.${p}`)).join("");
  return path.replace(/^\./, "") || "(top level)";
}

/** Every way `doc` breaks the schema, one line each; empty means valid. */
export function specErrors(doc: unknown): string[] {
  if (validate(doc)) return [];
  const all = validate.errors ?? [];
  // A stated sentence speaks for every error under the schema that states it.
  const stated = all.filter((e) => e.keyword === "errorMessage").map((e) => e.schemaPath.replace(/errorMessage$/, ""));
  const errors = all.filter((e) => e.keyword === "errorMessage" || !stated.some((s) => e.schemaPath.startsWith(s)));
  const inner = errors.filter((e) => !CHOICE.has(e.keyword));
  const shown = inner.length > 0 ? inner : errors;
  return [...new Set(shown.map((e) => `${where(e)}: ${e.message ?? e.keyword}`))];
}
