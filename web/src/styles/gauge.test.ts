/**
 * gauge.css — shape guards for the rules that moved here from my-resources.css
 * (the stat-row case came with them; a rule that moves brings its test).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const here = new URL(".", import.meta.url).pathname;
const css = readFileSync(join(here, "gauge.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const wide = css.replace(/@media[^{]*\{[\s\S]*?\n\}/g, "");

/** The declaration block of the rule whose selector is exactly `selector`. */
function rule(sheet: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = sheet.match(new RegExp(`(?:^|})\\s*${escaped}\\s*{([^}]*)}`));
  if (!m) throw new Error(`no rule for ${selector}`);
  return m[1];
}

describe("gauge.css", () => {
  it("lays the totals out as columns, not as a stack of full-width bars", () => {
    // Three 712px accent bars stacked in the same column as the rows beneath
    // them is what made /my-resources read as one seven-row list.
    const block = rule(wide, ".stat-row");
    expect(block).toMatch(/display:\s*grid/);
    // A `repeat(...)`, not a pinned count: the storage section puts ONE gauge
    // in this same panel, and the sandbox modal puts two.
    expect(block).toMatch(/grid-template-columns:\s*repeat\(/);
  });

  it("stacks the tiles at phone width — side by side they are narrower than the figures", () => {
    const narrow = css.match(/@media \(max-width: 640px\) \{([\s\S]*?)\n\}/);
    expect(narrow).not.toBeNull();
    expect(rule(narrow![1]!, ".stat-row")).toMatch(/grid-template-columns:\s*1fr/);
  });

  it("draws a live thing as a card — border, radius, surface — for the page's rows and the modal's status row alike", () => {
    const card = rule(wide, ".live-card");
    expect(card).toMatch(/border:\s*1px solid/);
    expect(card).toMatch(/border-radius:/);
    expect(card).toMatch(/background:/);
  });

  it("is not scoped to a page — the modal draws the same tiles", () => {
    expect(css).not.toMatch(/\.page\s/);
  });
});
