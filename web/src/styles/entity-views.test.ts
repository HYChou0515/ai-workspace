import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * #448 — the PM-app entity views (table / board / gantt / health) can't be
 * measured in happy-dom, so their look is guarded as a CSS drift check (same
 * approach as tokens.test.ts / kb-responsive.test.ts). This asserts the sheet
 * keeps the class contract the renderers depend on + stays token-clean; the
 * visual result itself is verified with Playwright against the real app.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(resolve(HERE, "entity-views.css"), "utf8");

describe("entity-views.css", () => {
  it("skins each view kind + shared write chrome", () => {
    for (const cls of [
      ".ev-panel",
      ".ev-table",
      ".ev-board",
      ".ev-card",
      ".ev-gantt__bar",
      ".ev-finding",
      ".ev-field",
      ".ev-quickcreate",
      ".ev-banner",
      ".ev-level",
      ".ev-empty",
    ]) {
      expect(CSS).toContain(cls);
    }
  });

  it("keeps the table calm — zebra + hover, chrome-on-interaction cells", () => {
    // zebra + hover (scoped with :not(.ev-table__group) so group headers keep
    // their own background — see #GH-projects A).
    expect(CSS).toMatch(/\.ev-table tbody tr[^{]*:nth-child\(even\)/);
    expect(CSS).toMatch(/\.ev-table tbody tr[^{]*:hover/);
    // inline cell fields reveal their border only on hover/focus.
    expect(CSS).toMatch(/\.ev-table tbody \.ev-field\s*\{[^}]*border-color:\s*transparent/);
  });

  it("gives the board real columns with a drop-target + degraded state (§D)", () => {
    expect(CSS).toContain(".ev-board__col--over");
    expect(CSS).toContain(".ev-board__col--degraded");
  });

  it("distinguishes gantt task bars from the today marker by colour", () => {
    const today = CSS.match(/\.ev-gantt__today\s*\{[^}]*\}/)?.[0] ?? "";
    const bar = CSS.match(/\.ev-gantt__bar\s*\{[^}]*\}/)?.[0] ?? "";
    expect(today).toMatch(/--accent/); // the today line stays the one red signal
    expect(bar).not.toMatch(/--accent\b/); // bars must not reuse it, or they blend in
  });

  it("skins every control in the view panel, not just the select", () => {
    // The panel styled its <select> and left the checkbox and the two
    // <input type="time"> boxes at browser defaults, so "Skip non-working
    // hours" and its time range sat in the middle of a designed popover
    // wearing whatever the OS paints — "幾乎沒有 css 樣式看起來很隨便".

    // The time range SHARES the select's rule rather than getting a second
    // copy: they are meant to look like the same control, and two rules that
    // must agree are two rules that will not.
    expect(CSS).toMatch(/\.ev-select[^{]*\.ev-viewpanel__range[^{]*\{/);

    // One field, not three: the border is on the GROUP and the inputs inside
    // are bare. Two separately-bordered boxes with "to" between them are three
    // controls for one value and did not fit the panel's narrow end.
    const rangeInput = CSS.match(/\.ev-viewpanel__range input\[type="time"\]\s*\{[^}]*\}/)?.[0] ?? "";
    expect(rangeInput).toMatch(/border:\s*0/);
    expect(rangeInput).toMatch(/background:\s*none/);
    // The picker glyphs cost ~32px of a 224px panel and duplicate the value.
    expect(CSS).toMatch(/::-webkit-calendar-picker-indicator\s*\{[^}]*display:\s*none/);

    // NOTHING in the panel wraps. Wrapping was tried and is worse: on the
    // shared field class it drops a checkbox's label below its box, and on the
    // range it makes the control reflow as the panel resizes. Anything too
    // wide is made to fit instead — shorter copy, or a field that shares one
    // border.
    for (const sel of ["\\.ev-viewpanel__field", "\\.ev-viewpanel__range"]) {
      const rule = CSS.match(new RegExp(sel + "\\s*\\{[^}]*\\}"))?.[0] ?? "";
      // A `not.toMatch` against "" passes having checked nothing, so renaming
      // or deleting the rule would retire this guard in silence. Prove the
      // rule was found BEFORE asserting what it does not contain.
      expect(rule, `${sel} has no rule to check`).not.toBe("");
      expect(rule, `${sel} must not wrap`).not.toMatch(/flex-wrap/);
    }

    const check = CSS.match(/\.ev-viewpanel__field input\[type="checkbox"\]\s*\{[^}]*\}/)?.[0] ?? "";
    expect(check).toMatch(/accent-color:\s*var\(--accent\)/);
    // A flex item's default is to shrink; the box then goes oval next to a
    // long label. It is the one thing in the row with a fixed size.
    expect(check).toMatch(/flex:\s*0 0 auto|flex-shrink:\s*0/);
  });

  it("truncates the first column instead of wrapping it", () => {
    // The gutter is a FIXED 150px beside rows of a FIXED height (GUTTER /
    // ROW_H / LANE_H), so a label that wraps has nowhere to put the second
    // line — it overlaps its neighbours. Three places already CLAIMED this
    // column truncates (the GutterRow comment, and two test names) while the
    // lane label had neither `nowrap` nor `overflow`, so a long group name
    // wrapped and the rows collided.
    const lane = CSS.match(/\.ev-gantt__lane-label\s*\{[^}]*\}/)?.[0] ?? "";
    expect(lane).toMatch(/white-space:\s*nowrap/);
    expect(lane).toMatch(/overflow:\s*hidden/);

    // `text-overflow` needs a BLOCK container: on a flex container it is
    // ignored, and it does not inherit into the anonymous flex item holding
    // the text. Both labels are flex (for the caret / vertical centring), so
    // the ellipsis has to live on an inner element — which is what this class
    // is for. Without it the text is chopped mid-glyph with no "…".
    const trunc = CSS.match(/\.ev-gantt__trunc\s*\{[^}]*\}/)?.[0] ?? "";
    expect(trunc).toMatch(/text-overflow:\s*ellipsis/);
    expect(trunc).toMatch(/overflow:\s*hidden/);
    expect(trunc).toMatch(/white-space:\s*nowrap/);
    // A flex item's automatic minimum is its CONTENT width, so without this it
    // refuses to shrink and overflows the gutter rather than ellipsising.
    expect(trunc).toMatch(/min-width:\s*0/);
  });

  it("only references declared design tokens (no hardcoded brand colors)", () => {
    // Guard against a stray hex on a fill — everything routes through tokens.
    // Box-shadows are the one sanctioned rgba() exception (no shadow token).
    const withoutShadows = CSS.replace(/box-shadow:[^;]*;/g, "");
    expect(withoutShadows).not.toMatch(/:\s*#[0-9a-fA-F]{3,6}\b/);
  });
});
