import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Affordance guard (#466): `.kb-chip` was declared twice — a passive label style
 * and a second interactive style with `cursor: pointer` — and the later rule won
 * for EVERY chip, so passive metadata labels ("private chat", "pinned") looked
 * exactly like the clickable toggle pills. Split the concerns: `.kb-chip` is a
 * passive LABEL (never clickable-looking); `.kb-chip--btn` carries the button
 * chrome (cursor + hover + the `.is-on` active state) and rides only on <button>.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const KB_CSS = resolve(HERE, "kb.css");

/** The body of the FIRST rule whose selector is exactly `sel` (`{...}`). */
function ruleBody(css: string, sel: string): string {
  const escaped = sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(css);
  return m ? m[1] : "";
}

describe("kb.css .kb-chip affordance split (#466)", () => {
  it("declares the bare `.kb-chip` label exactly once (no duplicate definition)", () => {
    const css = readFileSync(KB_CSS, "utf8");
    // A bare `.kb-chip {` — not `.kb-chip--x` / `.kb-chip.x` / `.kb-chip:x`.
    const bare = css.match(/\.kb-chip\s*\{/g) ?? [];
    expect(bare.length).toBe(1);
  });

  it("the passive `.kb-chip` label is NOT styled as clickable", () => {
    const css = readFileSync(KB_CSS, "utf8");
    expect(ruleBody(css, ".kb-chip")).not.toMatch(/cursor:\s*pointer/);
  });

  it("`.kb-chip--btn` carries the clickable button chrome", () => {
    const css = readFileSync(KB_CSS, "utf8");
    expect(ruleBody(css, ".kb-chip--btn")).toMatch(/cursor:\s*pointer/);
  });

  it("the accent active state hangs off the interactive class, not the bare label", () => {
    const css = readFileSync(KB_CSS, "utf8");
    expect(css).toMatch(/\.kb-chip--btn\.is-on\s*\{/);
  });
});
