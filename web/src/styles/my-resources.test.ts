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

/** `rule`, but over the sheet with every `@media` block removed — for a
 * selector that has a wide rule AND a narrow one, `rule` answers with whichever
 * comes first, and once the wide one is gone that is the narrow one: a guard
 * about the wide layout then reads the reflow's declarations and passes.
 *
 * Every media block, wherever it sits, rather than "the sheet before the
 * narrow one": wide rules live on both sides of it (the admin rules follow it),
 * and the breakpoint is not this test's to know (the header says a re-tune
 * must be free; the `reflows` guard already matches `\d+px`). Blocks end at
 * the first `}` on its own line, the same convention that guard uses. */
function wideRule(selector: string): string {
  const wide = css.replace(/@media[^{]*\{[\s\S]*?\n\}/g, "");
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = wide.match(new RegExp(`(?:^|})\\s*${escaped}\\s*{([^}]*)}`));
  if (!m) throw new Error(`no wide rule for ${selector}`);
  return m[1];
}

describe("my-resources: the live panel's layout", () => {
  it("gives a live sandbox a different row shape from a stored one", () => {
    // The complaint underneath all of this: two sections that mean different
    // things looked identical, and the only thing separating them was the word
    // on their buttons. The live rows are cards; the storage rows keep the
    // shared hairline-separated row.
    const live = rule(".page .live-list > li");
    expect(live).toMatch(/display:\s*grid/);
    // The card chrome (border-radius, surface) is `.live-card` in gauge.css —
    // guarded there — so this rule need only make the row a grid.
    expect(rule(".page ul:not(.live-list) > li")).toMatch(/display:\s*flex/);
  });

  it.each([".page .live-list", ".page .disk-list", ".page .wui-list"])(
    "declares %s's columns ONCE for the whole list, so the rows line up",
    (selector) => {
      // Declared on the ROW instead, every row sizes its own tracks: the spec
      // column is as wide as that row's own text, so "0.5 核 · 1.0 GB" and
      // "2 核 · 1.0 GB" produced different widths and pushed the fixed App
      // column to a different x on each row. The tags visibly failed to line
      // up in a column whose whole point is being scannable — and every DOM
      // test stayed green, because the tag renders either way.
      // `wideRule`, so a deleted wide rule cannot be answered for by the
      // narrow block's rule of the same name (it happens to say something
      // else today; that is luck, not a guard).
      const list = wideRule(selector);
      expect(list).toMatch(/display:\s*grid/);
      expect(list).toMatch(/grid-template-columns:/);
      // …and the row defers to it rather than re-declaring its own.
      expect(wideRule(`${selector} > li`)).toMatch(/grid-template-columns:\s*subgrid/);
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
    const tracks = wideRule(".page .live-list").match(/grid-template-columns:([^;]*);/)?.[1];
    expect(tracks).toBeTruthy();
    // dot · title · App · spec · action.
    const third = tracks!.trim().split(/\s+(?![^(]*\))/)[2];
    const LENGTH = String.raw`\d+(\.\d+)?(rem|px|ch|em)`;
    expect(third).toMatch(new RegExp(`^(${LENGTH}|fit-content\\(${LENGTH}\\))$`));
  });

  it("BOUNDS the overview's middle column, which is free text, so one long item title cannot zero every sibling's page title", () => {
    // Review round 2 of PR #811: the `/wui` list copied the disk list's tracks —
    // `minmax(0, 1fr) auto auto` — but its middle cell is an ITEM TITLE, not a
    // byte count. With the tracks shared down the list (subgrid) an `auto`
    // track sized itself to the longest item title in the group and the page
    // title, the only shrinkable track, paid on every row: measured, one
    // 43-character item title gave all five page titles in its group 0px at
    // 1280px.
    // The cap is what stops that; the reflow test below cannot see it, and
    // happy-dom lays nothing out.
    const tracks = wideRule(".page .wui-list").match(/grid-template-columns:([^;]*);/)?.[1];
    expect(tracks).toBeTruthy();
    const cols = tracks!.trim().split(/\s+(?![^(]*\))/);
    // mark · title · item + who/when · star · Remove (P13 put the mark first,
    // P14 the star before Remove). The free-text track is the THIRD; a test
    // that read "the second" after the mark arrived would have pinned the
    // title's `1fr` and called it a cap.
    expect(cols).toHaveLength(5);
    expect(cols.slice(3)).toEqual(["auto", "auto"]);
    expect(cols[2]).toMatch(/^fit-content\(\d+(\.\d+)?(%|rem|px|ch|em)\)$/);
    // The mark's track is `auto` and that is safe ONLY because the mark is a
    // fixed-size box — `PageMark` sets width and height inline, and its test
    // pins them. Nothing free-text may ever sit in an `auto` track here.
    expect(cols[0]).toBe("auto");
    // The circle clips what it holds (a file icon is `cover`ed, not stretched)
    // and never shrinks to make room — it is the one thing on the row with a
    // size of its own.
    const markRule = wideRule(".page .page-mark");
    expect(markRule).toMatch(/border-radius:\s*50%/);
    expect(markRule).toMatch(/overflow:\s*hidden/);
    expect(markRule).toMatch(/flex-shrink:\s*0/);
    // The cap alone bounds nothing: a grid track's automatic MINIMUM is the
    // cell's min-content, and for nowrap text that is the whole item title —
    // so the cell has to be allowed to shrink (`min-width: 0`) and to wrap.
    // Read from the WIDE half of the sheet: `rule()` returns the first match
    // in the file, and with the wide rule deleted it found the narrow block's
    // `.page .wui-list .detail` (which also says `white-space: normal`) and
    // passed on somebody else's declarations — round 3 of PR #811 showed the
    // round-2 defect fully back with this suite green.
    const detail = wideRule(".page .wui-list .detail");
    expect(detail).toMatch(/min-width:\s*0/);
    // …and what does not fit the cap is CUT with an ellipsis (P20, the
    // author's 「不要硬要顯示全部」 — reversing P6's wrap; the whole sentence
    // is in the cell's `title`). Still bounded: `overflow: hidden` and
    // `min-width: 0` are what keep a 60-letter token from running under
    // 下架 and scrolling the document (measured, round 3).
    expect(detail).toMatch(/white-space:\s*nowrap/);
    expect(detail).toMatch(/overflow:\s*hidden/);
    expect(detail).toMatch(/text-overflow:\s*ellipsis/);
  });

  it("lays the overview's cards out as a grid, and makes the whole card the link with the actions above it", () => {
    // The cards amendment: `AppCard`'s grid, copied. `auto-fill` + `minmax`,
    // or the cards never wrap.
    expect(wideRule(".page .wui-cards")).toMatch(/grid-template-columns:\s*repeat\(auto-fill,\s*minmax\(/);
    // The title's anchor is stretched over the card: without `inset: 0` on
    // its `::after` only the words are pressable and the card reads as a
    // link it is not. `position: relative` on the card is what the overlay
    // positions against — without it the overlay covers the PAGE.
    expect(wideRule(".page .wui-card")).toMatch(/position:\s*relative/);
    expect(wideRule(".page .wui-card .wui-card-text > a::after")).toMatch(/inset:\s*0/);
    expect(wideRule(".page .wui-card .wui-card-text > a::after")).toMatch(/position:\s*absolute/);
    // The actions sit ABOVE the overlay, or a press on the star opens the
    // page instead of starring it. Same for the item link in the detail.
    expect(wideRule(".page .wui-card > .wui-card-foot")).toMatch(/z-index:\s*[1-9]/);
    expect(wideRule(".page .wui-card .wui-card-text > .detail > a")).toMatch(/z-index:\s*[1-9]/);
    // Cards mode widens the shell (three cards at the Launcher's width).
    expect(wideRule(".page.page--wide")).toMatch(/max-width:\s*1080px/);
    // The star is the top-right corner, above the stretched link like the
    // footer actions are.
    expect(wideRule(".page .wui-card > .star")).toMatch(/position:\s*absolute/);
    expect(wideRule(".page .wui-card > .star")).toMatch(/z-index:\s*[1-9]/);
    // The body leaves the corner free, or the title runs under the star.
    expect(wideRule(".page .wui-card > .wui-card-body")).toMatch(/padding-right:\s*\d+px|padding:[^;]*\b(4[4-9]|[5-9]\d)px/);
  });

  it("makes every card in a row the same height, with the footer at the bottom", () => {
    // The author, on the P20 cards: 「應該要一樣高 比較整齊」. P19 had set
    // `align-items: start` because a stretched card beside a seven-line
    // title was 328px of nothing; P20's clamp bounds a card at two title
    // lines + one detail line, so stretching is safe again. The card is a
    // flex column with its body growing, or the footer floats mid-card.
    const grid = wideRule(".page .wui-cards");
    expect(grid).not.toMatch(/align-items:\s*start/);
    const card = wideRule(".page .wui-card");
    expect(card).toMatch(/display:\s*flex/);
    expect(card).toMatch(/flex-direction:\s*column/);
    // Said out loud, because the shell's `.page ul > li` is a CENTRED flex
    // row: the first cut of this column inherited `align-items: center` and
    // the stripe went 0px wide while the body was clipped on both sides
    // (measured on the real page). Both are the shell's to override.
    expect(card).toMatch(/align-items:\s*stretch/);
    expect(card).toMatch(/gap:\s*0/);
    const body = wideRule(".page .wui-card > .wui-card-body");
    expect(body).toMatch(/flex:\s*1/);
    expect(body).toMatch(/min-width:\s*0/);
  });

  it("fills a pressed star in EVERY view, not only the table", () => {
    // P14 scoped the fill to `.wui-list`; the cards (P19) drew every starred
    // page hollow — measured on the P20 harness, a pressed star with no fill.
    const rule = wideRule('.page [aria-pressed="true"] [data-icon="star"] path');
    expect(rule).toMatch(/fill:\s*currentColor/);
    expect(css).not.toMatch(/\.wui-list \[aria-pressed="true"\] \[data-icon="star"\]/);
  });

  it("cuts a long title and a long detail instead of showing them whole", () => {
    // The author, on the cards: 「當 title 太長或是描述太長 不要硬要顯示全部」.
    // The card title is clamped to two lines, its detail and the table's
    // detail to one with an ellipsis; the full text is in a `title`. On the
    // table this REVERSES P6's `white-space: normal` (an ellipsis was
    // thought to hide who put the page up) — the author's call.
    const cardTitle = wideRule(".page .wui-card .wui-card-text > a");
    expect(cardTitle).toMatch(/-webkit-line-clamp:\s*2/);
    expect(cardTitle).toMatch(/display:\s*-webkit-box/);
    expect(cardTitle).toMatch(/overflow:\s*hidden/);
    for (const sel of [".page .wui-card .wui-card-text > .detail", ".page .wui-list .detail"]) {
      const r = wideRule(sel);
      expect(r).toMatch(/white-space:\s*nowrap/);
      expect(r).toMatch(/overflow:\s*hidden/);
      expect(r).toMatch(/text-overflow:\s*ellipsis/);
      // The ellipsis needs the box to be allowed to shrink (`reference_flex_kills_text_overflow`).
      expect(r).toMatch(/min-width:\s*0/);
    }
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
    // Three lists now, not two: the overview (`/wui`) shipped with the shell
    // and no reflow of its own, and this guard named the other two — the
    // exact half-somebody-tested shape the comment above warns about. Its
    // title measured 0px at 390px in a real browser (PR #811, review round 1).
    expect(block).toMatch(/\.page \.wui-list[^{}]*\{[^}]*display:\s*flex/);
    expect(block).toMatch(/\.page \.wui-list > li \{[^}]*grid-template-columns:/);
    expect(block).toMatch(/\.page \.wui-list \.detail \{[^}]*grid-row:\s*2/);
    // …and under the TITLE, not under the mark: the mark keeps column 1, so a
    // detail that spanned from column 1 would start under the circle and read
    // as a third thing on the row rather than the title's second line.
    expect(block).toMatch(/\.page \.wui-list \.detail \{[^}]*grid-column:\s*2 \/ -1/);
    expect(block).toMatch(/\.page \.wui-list > li > \.page-mark \{[^}]*grid-column:\s*1/);
    // The star keeps column 3 and Remove column 4 by the one attribute that
    // tells them apart, so a reader's row (no Remove) still puts its star
    // where every other row's is.
    expect(block).toMatch(/\.page \.wui-list > li > button\[aria-pressed\] \{[^}]*grid-column:\s*3/);
    expect(block).toMatch(/\.page \.wui-list > li > button:not\(\[aria-pressed\]\) \{[^}]*grid-column:\s*4/);
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
