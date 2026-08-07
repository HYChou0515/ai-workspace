/**
 * Read declarations straight out of a stylesheet, honouring last-wins.
 *
 * Extracted from `ganttAxisSticky.test.ts` (#690 P7) when a second guard
 * needed it. The reason these guards read CSS text instead of the DOM is
 * worth keeping in one place: the test environment does not lay out or
 * cascade, so `getComputedStyle` will happily report a value that a later
 * declaration in the SAME block overrides — which is the exact defect the
 * sticky guard exists for.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const ENTITY_VIEWS_CSS = readFileSync(
  fileURLToPath(new URL("../styles/entity-views.css", import.meta.url)),
  "utf8",
);

/** The declarations of one rule, in source order. */
export function ruleBody(css: string, selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at < 0) throw new Error(`${selector} is not in the stylesheet`);
  return css.slice(at, css.indexOf("\n}", at));
}

/**
 * The declaration that wins inside one rule — the LAST one, because the
 * cascade within a block is last-wins. Returns undefined if the rule never
 * sets the property.
 */
export function effective(css: string, selector: string, property: string): string | undefined {
  const hits = [
    ...ruleBody(css, selector).matchAll(new RegExp(`^\\s*${property}\\s*:\\s*([^;]+);`, "gm")),
  ];
  return hits.at(-1)?.[1].trim();
}

/**
 * The colour an element paints its text in, given the rules that can set it.
 * `color: inherit` walks to the next selector in the chain, which is how the
 * bar's contents pick up whichever ink the bar itself carries.
 */
export function inheritedColor(css: string, chain: string[]): string | undefined {
  for (const selector of chain) {
    const c = effective(css, selector, "color");
    if (c && c !== "inherit") return c;
  }
  return undefined;
}
