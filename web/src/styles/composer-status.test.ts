import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * The composer status row's separators (docs/plan-llm-budget.md As built; the
 * user: 「擺不下才換行」). Cells share a line and wrap only when there is no room;
 * every cell keeps a left margin with its 「·」 drawn in it, the row is pulled
 * left by that margin and its left edge clipped, so the dot of a cell that
 * starts a line is cut off and every other dot shows.
 *
 * Two regressions went through this unpinned in review (a cell's own wrapped
 * line clipped; the KB row's `margin` shorthand undoing the pull), and a third
 * (the keyboard focus ring cut on the left). The numbers here are DERIVED from
 * each other and from the global `:focus-visible` rule, not restated — so a
 * change to any one of them that breaks the relation reddens this.
 */
const HERE = dirname(fileURLToPath(import.meta.url));
const base = readFileSync(resolve(HERE, "base.css"), "utf8");
const kb = readFileSync(resolve(HERE, "kb.css"), "utf8");

function block(css: string, selector: string): string {
  const esc = selector.replace(/[.*+?^${}()|[\]\\>]/g, "\\$&").replace(/\s+/g, "\\s*");
  const m = new RegExp(`(^|\\n)${esc}\\s*\\{([^}]*)\\}`).exec(css);
  expect(m, `no rule for ${selector}`).not.toBeNull();
  return m?.[2] ?? "";
}
const px = (decl: string | undefined) => Number(/(-?[\d.]+)px/.exec(decl ?? "")?.[1]);
const prop = (body: string, name: string) => new RegExp(`(^|[;\\s])${name}\\s*:\\s*([^;]+);`).exec(body)?.[2];

describe(".composer-status separators", () => {
  const row = block(base, ".composer-status");
  const cell = block(base, ".composer-status > *");
  const dot = block(base, ".composer-status > *::before");
  const slot = px(prop(cell, "margin-left"));

  it("pulls the row left by exactly the slot every cell keeps", () => {
    expect(slot).toBeGreaterThan(0);
    expect(px(prop(row, "margin-left"))).toBe(-slot);
  });

  it("draws the dot in that slot, out of the cell's own layout", () => {
    expect(prop(dot, "position")).toBe("absolute");
    expect(prop(dot, "right")).toBe("100%");
    expect(px(prop(dot, "width"))).toBe(slot);
    expect(prop(cell, "position")).toBe("relative");
  });

  it("clips past the dot of a line-starting cell, but not into its focus ring", () => {
    const clip = prop(row, "clip-path") ?? "";
    const insets = /inset\(([^)]*)\)/.exec(clip)?.[1].split(/\s+/).map((v) => Number(v.replace("px", ""))) ?? [];
    expect(insets).toHaveLength(4);
    const left = insets[3];
    // The dot glyph sits at the slot's centre; a few px either side is the glyph.
    expect(left).toBeGreaterThanOrEqual(slot / 2 + 3);
    // The global keyboard ring reaches outline-width + outline-offset outside a cell.
    const ring = block(base, ":focus-visible");
    const reach = px(prop(ring, "outline")) + px(prop(ring, "outline-offset"));
    expect(reach).toBeGreaterThan(0);
    expect(left).toBeLessThanOrEqual(slot - reach);
    // …and on the other three sides the clip stands off by at least that much.
    for (const side of insets.slice(0, 3)) expect(side).toBeLessThanOrEqual(-reach);
  });

  it("lets a full-width status line carry no dot", () => {
    expect(prop(block(base, ".composer-status > [data-status-line]::before"), "content")).toBe("none");
  });

  it("is not undone in the knowledge-base chat by a margin shorthand", () => {
    const kbRow = block(kb, ".kb-composer-status");
    expect(prop(kbRow, "margin")).toBeUndefined();
    expect(prop(kbRow, "margin-left")).toBeUndefined();
  });
});
