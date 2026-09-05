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

  it("BOUNDS the App column so a long name cannot eat the title", () => {
    // `auto` would size the column to the longest App name in view, which is
    // both variable and unbounded — the title is what must give way, and only
    // because it can ellipsis. The property is the CEILING, not a fixed width:
    // a hard `10rem` also reserved 78px that the shipped names never use, and
    // `minmax(0, 1fr)` — the only shrinkable track — paid for it (193px of
    // title at a 641px viewport). `fit-content(10rem)` keeps the cap and hands
    // the slack back, and still gives every row one shared track.
    const tracks = rule(".page .live-list").match(/grid-template-columns:([^;]*);/)?.[1];
    expect(tracks).toBeTruthy();
    // dot · title · App · spec · action.
    const third = tracks!.trim().split(/\s+(?![^(]*\))/)[2];
    const LENGTH = String.raw`\d+(\.\d+)?(rem|px|ch|em)`;
    expect(third).toMatch(new RegExp(`^(${LENGTH}|fit-content\\(${LENGTH}\\))$`));
  });

  it("reflows the rows before the fixed columns eat the title", () => {
    // The columns reserve ~400px before the title gets any, and the title is
    // the only shrinkable track — measured in Chromium it reached width 0 at a
    // 390px viewport, leaving rows nobody can identify and a Close button that
    // still works on them. Nothing else here can see that: happy-dom lays
    // nothing out, and the guards above read declarations, not geometry.
    //
    // So this pins the ESCAPE HATCH's existence. Whether it is wide enough is a
    // measurement, and the measurement lives outside CI — but a media query
    // deleted in a refactor is the failure this can actually catch.
    const narrow = css.match(/@media \(max-width: \d+px\) \{([\s\S]*?)\n\}/);
    expect(narrow).not.toBeNull();
    const block = narrow![1];
    // Both lists have to let go of the wide fixed tracks, or the reflow only
    // fixes the half somebody happened to test.
    expect(block).toMatch(/\.page \.live-list[^{}]*\{[^}]*display:\s*flex/);
    // `[^}]*`, never `[\s\S]*`: the greedy form runs past this rule's closing
    // brace and satisfies itself from a later rule in the same media block, so
    // deleting the declaration it names changes nothing. Verified: with
    // `[\s\S]*` the storage row lost its narrow tracks and rendered a 256px
    // 刪除 button on the first line, with the suite green.
    expect(block).toMatch(/\.page \.disk-list > li \{[^}]*grid-template-columns:/);
    // …and the title must stop sharing a line with the App tag, which is what
    // gives it the width back.
    expect(block).toMatch(/\.page \.live-list \.app-tag \{[^}]*grid-row:\s*2/);
  });

  it("actually APPLIES the dark ink, not just computes one", () => {
    // Found by mutation: deleting this rule left all the other guards green.
    // `appColor.test.ts` proves both inks clear the contrast floor and says
    // nothing about whether the stylesheet ever reaches for the dark one — and
    // without it every pill wears the ink tuned for cream on an ink surface,
    // which is the entire reason a second value exists. The defect is invisible
    // in light mode, so it is also invisible to anyone not looking for it.
    expect(rule('[data-theme="dark"] .page .app-tag')).toMatch(/color:\s*var\(--app-ink-dark/);
    // …and the light rule reaches for the other one, so the two cannot collapse
    // into a single value that is wrong in one theme.
    // `[,)]` and NOT `\b`: a word boundary matches at the hyphen, so
    // `var(--app-ink\b` is satisfied by `var(--app-ink-dark` — this guard
    // accepted the very collapse it exists to forbid, and pointing BOTH rules
    // at the dark ink left all 3547 tests green while every light-theme pill
    // wore #ffb19f on cream (~1.5:1, unreadable).
    expect(rule(".page .app-tag")).toMatch(/color:\s*var\(--app-ink[,)]/);
  });

  it("stops the row's failure message landing in the 8px dot column", () => {
    // The alert is a fifth child of a five-column grid, so without an explicit
    // span it drops into the first cell of an implicit second row — which is
    // the dot's column, 8px wide. The DOM test that asserts the message appears
    // on the right row passes either way.
    expect(rule(".page .live-list .error")).toMatch(/grid-column:\s*1\s*\/\s*-1/);
  });
});
