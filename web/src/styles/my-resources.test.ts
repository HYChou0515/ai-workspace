import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * The live-environment panel's LAYOUT decisions, guarded here because no other
 * test in this repo can see them.
 *
 * `MyResourcesPage.test.tsx` runs on happy-dom, which applies no stylesheet: it
 * asserts that the three totals sit in a named group and that each row names its
 * App, and every one of those assertions stays green with this file deleted —
 * at which point the section is back to what it was, seven full-width rows in
 * one column, the totals indistinguishable from the environments below them.
 * That is not hypothetical here: this page shipped once with a complete
 * vocabulary of semantic classes and NO stylesheet at all, and passed every
 * "is it clickable" check while its meters were two zero-height divs.
 *
 * So these pin the three decisions whose loss is invisible to the DOM tests and
 * visible to a reader. They assert the SHAPE of the decision, not exact pixel
 * values — a re-tune should be free, a silent reversion should not.
 */
const here = new URL(".", import.meta.url).pathname;
// Comments stripped FIRST. This sheet documents its reasoning heavily, so a
// rule is usually preceded by a comment rather than by the previous rule's `}`
// — and the anchor below needs one or the other. Leaving them in made this
// helper report "no rule for .page .stat-row" about a rule that was right
// there, which reads as the product being broken when it is the probe.
const css = readFileSync(join(here, "my-resources.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

/** The declaration block of the rule whose selector is exactly `selector`. */
function rule(selector: string): string {
  // Anchored on a preceding `}` or start-of-file so that asking for
  // `.page .live-list > li` cannot match inside `.page .live-list > li:hover`
  // or `... > li > a` — the near-miss that would make one of these guards read
  // a neighbouring rule and pass on somebody else's declarations.
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = css.match(new RegExp(`(?:^|})\\s*${escaped}\\s*{([^}]*)}`));
  if (!m) throw new Error(`no rule for ${selector}`);
  return m[1];
}

describe("my-resources: the live panel's layout", () => {
  it("lays the three totals out as columns, not as a stack of full-width bars", () => {
    // Three 712px accent bars stacked in the same column as the rows beneath
    // them is what made the section read as one seven-row list. The group role
    // the DOM test asserts survives this rule being deleted; the separation
    // does not.
    const block = rule(".page .stat-row");
    expect(block).toMatch(/display:\s*grid/);
    expect(block).toMatch(/grid-template-columns:\s*repeat\(3,/);
  });

  it("gives a live environment a different row shape from a stored one", () => {
    // The complaint underneath all of this: two sections that mean different
    // things looked identical, and the only thing separating them was the word
    // on their buttons. The live rows are cards; the storage rows keep the
    // shared hairline-separated row.
    const live = rule(".page .live-list > li");
    expect(live).toMatch(/display:\s*grid/);
    expect(live).toMatch(/border-radius:/);
    expect(rule(".page ul > li")).toMatch(/display:\s*flex/);
  });

  it.each([".page .live-list", ".page .disk-list"])(
    "declares %s's columns ONCE for the whole list, so the rows line up",
    (selector) => {
      // Declared on the ROW instead, every row sizes its own tracks: the spec
      // column is as wide as that row's own text, so "0.5 核 · 1.0 GB" and
      // "2 核 · 1.0 GB" produced different widths and pushed the fixed App
      // column to a different x on each row. The tags visibly failed to line
      // up in a column whose whole point is being scannable — and every DOM
      // test stayed green, because the tag renders either way.
      const list = rule(selector);
      expect(list).toMatch(/display:\s*grid/);
      expect(list).toMatch(/grid-template-columns:/);
      // …and the row defers to it rather than re-declaring its own.
      expect(rule(`${selector} > li`)).toMatch(/grid-template-columns:\s*subgrid/);
    },
  );

  it("holds the App column to a fixed width so a long name cannot eat the title", () => {
    // `auto` here would size the column to the longest App name in view, which
    // is both variable and unbounded — the title is what must give way, and
    // only because it can ellipsis.
    const tracks = rule(".page .live-list").match(/grid-template-columns:([^;]*);/)?.[1];
    expect(tracks).toBeTruthy();
    // dot · title · App · spec · action — the third track is a fixed length.
    const third = tracks!.trim().split(/\s+(?![^(]*\))/)[2];
    expect(third).toMatch(/^\d+(\.\d+)?(rem|px|ch|em)$/);
  });

  it("stops the row's failure message landing in the 8px dot column", () => {
    // The alert is a fifth child of a five-column grid, so without an explicit
    // span it drops into the first cell of an implicit second row — which is
    // the dot's column, 8px wide. The DOM test that asserts the message appears
    // on the right row passes either way.
    expect(rule(".page .live-list .error")).toMatch(/grid-column:\s*1\s*\/\s*-1/);
  });
});
