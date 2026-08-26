/**
 * #715: the import progress has to be VISIBLE, not merely rendered.
 *
 * It shipped naming four classes — `kb-import-status`, `__bar`, `__fill`, and a
 * `kb-btn--ghost` that never existed — and none of them were styled. The bar was
 * two empty spans with no height and no background, so a person watching an
 * import saw nothing, and every unit test passed straight through it: happy-dom
 * has no layout, so "is this visible" is not a question it can answer.
 *
 * These assert the one thing a DOM test CAN answer honestly — that the class a
 * component names is actually styled somewhere — which is the half that was
 * missing. The other half (a real box, in a real browser) is not fakeable here.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(join(__dirname, "kb.css"), "utf8");

/** The declarations of one rule, with comments stripped.
 *
 * A rule's comment often NAMES the thing the rule exists not to do — this file
 * asserts "no `minmax`" against a block whose comment explains why `minmax` was
 * wrong. Matching the raw text makes the explanation fail the test. */
function declarations(selector: string): string {
  const from = CSS.indexOf(selector);
  if (from < 0) return "";
  const block = CSS.slice(from, CSS.indexOf("}", from));
  return block.replace(/\/\*[\s\S]*?\*\//g, "");
}

const REQUIRED = [
  ".kb-import-status",
  ".kb-import-status__text",
  ".kb-import-status__bar",
  ".kb-import-status__fill",
  ".kb-import-status__errors",
];

/** Does a RULE exist for this selector?
 *
 * Not `toContain`: as a substring, `.kb-import-status` is satisfied by
 * `.kb-import-status__text` and by a typo like `.kb-import-status-GONE`, so the
 * first version of this file passed with the whole block renamed away. The
 * selector has to be followed by something that ends it — `{`, `,`, `[`, `:`. */
function hasRule(selector: string): boolean {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // Anything that ENDS the selector: a brace, a comma, an attribute or pseudo,
  // or whitespace/a combinator introducing a descendant — `__errors` is styled
  // only through `__errors summary` and `__errors ul`, which is still styled.
  // What must NOT satisfy it is a longer NAME: `-GONE` starts with `-`, so a
  // renamed-away rule still fails, which is what the control proves.
  return new RegExp(`${escaped}[\\s,{[:>+~]`).test(CSS);
}

describe("archive import progress (#715)", () => {
  it.each(REQUIRED)("styles %s", (selector) => {
    expect(hasRule(selector)).toBe(true);
  });

  it("the rule check cannot be satisfied by a longer name that starts the same", () => {
    // The check that guards the checks: this is exactly how the first version
    // of this file passed while the styles it names had been renamed away.
    expect(hasRule(".kb-import-status-does-not-exist")).toBe(false);
  });

  it("gives the bar a height, or it draws nothing", () => {
    // A zero-height box is the exact failure this file exists for: present in
    // the DOM, absent on the screen.
    const rule = CSS.slice(CSS.indexOf(".kb-import-status__bar"));
    const block = rule.slice(0, rule.indexOf("}"));
    expect(block).toMatch(/height:\s*[1-9]/);
  });

  it("gives the fill a background, or it is an invisible box", () => {
    const rule = CSS.slice(CSS.indexOf(".kb-import-status__fill"));
    const block = rule.slice(0, rule.indexOf("}"));
    expect(block).toMatch(/background:/);
  });

  it("distinguishes a finished import from a half-applied one", () => {
    // `finished` is not `succeeded`. If both states look the same, the run that
    // dropped documents reads as a clean import — the silence #715 removes.
    expect(hasRule('.kb-import-status[data-state="done"]')).toBe(true);
    expect(hasRule('.kb-import-status[data-state="partial"]')).toBe(true);
  });
});

describe("card attachments (#730)", () => {
  it("lays attachments out as a grid, not a wrapped row", () => {
    const rule = CSS.slice(CSS.indexOf(".kb-cards__attachments"));
    const block = rule.slice(0, rule.indexOf("}"));
    expect(block).toMatch(/display:\s*grid/);
    expect(block).toMatch(/grid-template-columns:/);
  });

  it("does not crop the picture", () => {
    // `cover` on a defect photograph hides the defect: the mark is as often at
    // an edge as in the middle, so every cropped thumbnail looks like the next
    // one and a person has to open each in turn to tell them apart.
    const rule = CSS.slice(CSS.indexOf(".kb-cards__tile-img"));
    const block = rule.slice(0, rule.indexOf("}"));
    expect(block).toMatch(/object-fit:\s*contain/);
    expect(block).not.toMatch(/object-fit:\s*cover/);
  });

  it("leaves no styles behind for markup that no longer exists", () => {
    // The old cropped-square rules had no elements left to style. Dead CSS is
    // how the next reader concludes a thumbnail is still 72px.
    expect(CSS).not.toContain(".kb-cards__thumb-wrap");
    expect(CSS).not.toContain(".kb-cards__thumb-open");
  });
});

describe("attachment size control (#730)", () => {
  it("makes the slider value the tile WIDTH, not a minmax floor", () => {
    // With `minmax(var(--kb-tile), 1fr)` the rendered width is container ÷
    // column count — an integer — so dragging the slider did nothing until the
    // count changed and then jumped a whole step. Measured in a real browser:
    // 96 / 132 / 224px come out exactly, only because there is no `1fr`.
    const block = declarations(".kb-cards__attachments");
    expect(block).toMatch(/repeat\(auto-fill,\s*var\(--kb-tile/);
    expect(block).not.toMatch(/minmax/);
    expect(block).not.toMatch(/1fr/);
  });

  it("pins the control to the right of the row", () => {
    // In preview there is no label to push against, so the row itself has to
    // align — relying on the label left the control stranded mid-row.
    const block = declarations(".kb-cards__attach-head");
    expect(block).toMatch(/justify-content:\s*flex-end/);
  });
});
