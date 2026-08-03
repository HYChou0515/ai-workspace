import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * #690 P7 — the axis must stay put while a long project scrolls under it.
 *
 * This reads the stylesheet rather than the DOM because the test environment
 * does not lay out: `getComputedStyle` there will happily report `sticky` for
 * a rule that a later declaration in the SAME block overrides, and that is
 * precisely the defect this file exists for — the sticky rules were added
 * above an existing `position: relative`, the last one won, and every
 * structural assertion in GanttView.test.tsx still passed while the header
 * scrolled away. A real browser found it; this keeps it found.
 *
 * What it cannot show is that the header holds — that needs layout. The
 * browser measurement is recorded in docs/plan-pm-gantt-urgency-and-axis.md §7.
 */
const CSS = readFileSync(fileURLToPath(new URL("../../styles/entity-views.css", import.meta.url)), "utf8");

/** The declarations of one rule, in source order — the cascade within a block
 * is last-wins, so order is the whole point. */
function ruleBody(selector: string): string {
  const at = CSS.indexOf(`${selector} {`);
  expect(at, `${selector} is not in entity-views.css`).toBeGreaterThan(-1);
  return CSS.slice(at, CSS.indexOf("\n}", at));
}

function effective(selector: string, property: string): string | undefined {
  const hits = [...ruleBody(selector).matchAll(new RegExp(`^\\s*${property}\\s*:\\s*([^;]+);`, "gm"))];
  return hits.at(-1)?.[1].trim();
}

describe("the gantt axis sticks to the top of the chart", () => {
  it("is positioned sticky, after every other position declaration in its rule", () => {
    expect(effective(".ev-gantt__axis", "position")).toBe("sticky");
    expect(effective(".ev-gantt__axis", "top")).toBe("0");
  });

  it("is opaque, so rows scrolling under it do not show through", () => {
    // Sticky only lifts the axis out of flow; without a background the bars
    // pass through it and the dates become unreadable — the thing P7 is for.
    expect(effective(".ev-gantt__axis", "background")).toBeDefined();
    expect(effective(".ev-gantt__axis", "background")).not.toContain("transparent");
  });

  it("sits above the bars it overlaps", () => {
    const axis = Number(effective(".ev-gantt__axis", "z-index"));
    expect(axis).toBeGreaterThan(0);
  });

  it("scrolls inside the chart, not the page, or there is nothing to stick to", () => {
    expect(effective(".ev-gantt__scroll", "overflow")).toBe("auto");
    // A scroller with no ceiling grows to fit its content and never scrolls;
    // the axis would then be sticky against a box that never moves.
    expect(effective(".ev-gantt__scroll", "max-height")).toBeDefined();
  });
});
