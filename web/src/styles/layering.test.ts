import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * The stacking scale, guarded as RELATIONSHIPS rather than literals (#pm).
 *
 * A menu opened from a piece of furniture has to paint above it. The chat
 * switcher's dropdown did not: it carried a hand-written `z-index: 20` while
 * the chat rail becomes an overlay at 42 on a narrow viewport, so on those
 * widths the menu opened *underneath* the rail. Both numbers were fine in
 * isolation, which is why nothing caught it — the defect only exists in the
 * comparison.
 *
 * Asserting the ORDER, not the values, is what keeps this useful if the scale is
 * ever re-tuned.
 */
const here = new URL(".", import.meta.url).pathname;
const read = (f: string) => readFileSync(join(here, f), "utf8");

function tokenValue(css: string, name: string): number {
  const m = css.match(new RegExp(`${name}:\\s*(\\d+)`));
  if (!m) throw new Error(`token ${name} not found`);
  return Number(m[1]);
}

describe("stacking scale", () => {
  const tokens = read("tokens.css");

  it("puts menus above the furniture they open from", () => {
    expect(tokenValue(tokens, "--z-popover")).toBeGreaterThan(tokenValue(tokens, "--z-rail"));
  });

  it("keeps modals above menus, and dialogs above modals", () => {
    const popover = tokenValue(tokens, "--z-popover");
    const modal = tokenValue(tokens, "--z-modal");
    expect(modal).toBeGreaterThan(popover);
    expect(tokenValue(tokens, "--z-dialog")).toBeGreaterThan(modal);
  });

  it.each([".chat-switcher__menu", ".new-item-picker__menu", ".wf-launch-menu__menu"])(
    "opens %s on the popover layer",
    (selector) => {
      // Scoping this to the switcher alone was the original miss: its two
      // siblings in the same bar carried the same hand-written 20, so the
      // `New…` dropdown still opened under the rail after the switcher was fixed.
      const rule = read("topic-hub.css").split(selector)[1]?.split("}")[0] ?? "";
      expect(rule).toMatch(/--z-popover/);
    },
  );

  it("leaves no hand-written z-index in the rail or the chat furniture", () => {
    // The values are the scale's job. A literal here is how the two drifted
    // apart in the first place.
    expect(read("chat-rail.css")).not.toMatch(/z-index:\s*\d/);
    expect(read("topic-hub.css")).not.toMatch(/z-index:\s*\d/);
  });
});
