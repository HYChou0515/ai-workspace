/**
 * item-environment.css — shape guards for the Sandbox modal's layout. They
 * assert the decision, not the pixel: a re-tune is free, a silent reversion
 * is not. Each rule here answers a measured defect (see the plan's live-check
 * record): figures breaking mid-number beside the Close button at 390, and two
 * fields of different widths.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const here = new URL(".", import.meta.url).pathname;
const css = readFileSync(join(here, "item-environment.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const wide = css.replace(/@media[^{]*\{[\s\S]*?\n\}/g, "");
// The invalid-field look is the house input's (base.css), not this panel's
// (#829 D8); the fields wear `input input--block` rather than a scoped copy.
const base = readFileSync(join(here, "base.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

function rule(sheet: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = sheet.match(new RegExp(`(?:^|})\\s*${escaped}\\s*{([^}]*)}`));
  if (!m) throw new Error(`no rule for ${selector}`);
  return m[1];
}

describe("item-environment.css: the status row", () => {
  it("wraps the BUTTON to its own line rather than breaking the figures — they never shrink", () => {
    expect(rule(wide, ".item-environment .env-status")).toMatch(/flex-wrap:\s*wrap/);
    const detail = rule(wide, ".item-environment .env-status__detail");
    expect(detail).toMatch(/white-space:\s*nowrap/);
    // `flex: 1 0 auto` — grows, never shrinks; `min-width: 0` let the text
    // run under the button.
    expect(detail).toMatch(/flex:\s*1\s+0\s+auto/);
    expect(detail).not.toMatch(/min-width:\s*0/);
  });

  it("borrows the live card, declaring no border or surface of its own", () => {
    const row = rule(wide, ".item-environment .env-status");
    expect(row).not.toMatch(/border:/);
    expect(row).not.toMatch(/background:/);
  });
});

describe("item-environment.css: the size fields", () => {
  it("are two equal columns, and one column at phone width", () => {
    expect(rule(wide, ".item-environment .env-fields")).toMatch(/grid-template-columns:\s*repeat\(2,/);
    const narrow = css.match(/@media \(max-width: 480px\) \{([\s\S]*?)\n\}/);
    expect(narrow).not.toBeNull();
    expect(rule(narrow![1]!, ".item-environment .env-fields")).toMatch(/grid-template-columns:\s*1fr/);
  });

  it("fill their column — the house input's `flex: 1` is for rows", () => {
    // `.input--block` (base.css) is the one spelling of "fill the width in a
    // column"; both fields wear it, and this sheet keeps no copy.
    const panel = readFileSync(join(here, "../components/ItemEnvironmentPanel.tsx"), "utf8");
    expect(panel.match(/className="input input--block"/g)?.length).toBe(2);
    expect(wide).not.toMatch(/\.input(?![\w-])/);
  });

  it("show an invalid value in the error colour, on the field and on the note", () => {
    expect(rule(base, '.input[aria-invalid="true"]')).toMatch(/border-color:\s*var\(--err\)/);
    expect(rule(wide, ".item-environment .env-field__note--invalid")).toMatch(/color:\s*var\(--err\)/);
    expect(wide).not.toMatch(/aria-invalid/);
  });

  it("still show focus on an invalid field — the red border hides the accent one, so a ring", () => {
    expect(rule(base, '.input[aria-invalid="true"]:focus')).toMatch(/box-shadow:/);
  });
});
