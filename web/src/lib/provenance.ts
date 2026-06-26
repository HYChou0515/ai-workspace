/**
 * #254 — render a citation's aggregated source location into a short,
 * localized chip ("Page 3–4 · Failure Analysis > Root Cause").
 *
 * Mirrors the backend `kb/provenance.format_location`, but the locator words
 * are translated (the i18n layer has no interpolation, so each is a plain
 * prefix). The section breadcrumb is the doc's own text and passes through
 * verbatim. Contiguous page/line runs collapse to a range only here, at render
 * time — the wire format keeps the distinct values.
 */
import type { Provenance } from "../api/types";
import type { MsgKey } from "./i18n";

// Display order + the label key for each known locator. `null` = no prefix
// (the value is self-describing, e.g. a section breadcrumb or sheet-less name).
const LOCATORS: Array<[string, MsgKey | null]> = [
  ["page", "cite.loc.page"],
  ["slide", "cite.loc.slide"],
  ["sheet", "cite.loc.sheet"],
  ["section", null],
  ["jsonl_line", "cite.loc.line"],
  ["row", "cite.loc.row"],
];

function renderValues(values: Array<string | number>): string {
  if (values.length > 1 && values.every((v) => typeof v === "number")) {
    const nums = values as number[];
    const lo = Math.min(...nums);
    const hi = Math.max(...nums);
    if (hi - lo === values.length - 1) return `${lo}–${hi}`; // gap-free run
  }
  return values.map(String).join(", ");
}

export function formatProvenance(
  provenance: Provenance | undefined,
  t: (k: MsgKey) => string,
): string {
  if (!provenance) return "";
  const parts: string[] = [];
  const seen = new Set<string>();
  const known = new Set(LOCATORS.map(([k]) => k));
  for (const [key, labelKey] of LOCATORS) {
    const values = provenance[key];
    if (!values || values.length === 0 || seen.has(key)) continue;
    seen.add(key);
    const rendered = renderValues(values);
    parts.push(labelKey ? `${t(labelKey)} ${rendered}` : rendered);
  }
  // Unknown future locators: never silently dropped — prefix with the raw key.
  for (const key of Object.keys(provenance)) {
    if (known.has(key)) continue;
    const values = provenance[key];
    if (!values || values.length === 0) continue;
    parts.push(`${key} ${renderValues(values)}`);
  }
  return parts.join(" · ");
}
