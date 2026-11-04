/**
 * chat-feed.css — an entry of a long chat that is off screen is not laid out
 * (the long-chat perf fix, P2).
 *
 * Opening a 300-turn chat spent 2.4s in layout and style for 114k DOM nodes.
 * `content-visibility: auto` lets the browser skip an off-screen entry's layout
 * and paint while keeping it in the DOM, so the browser's find-in-page still
 * finds every message (the user chose this over drawing only the latest turns).
 * Each rule needs an intrinsic size to stand in for the skipped entry, or the
 * scrollbar collapses; `auto` makes the browser keep the real size once drawn.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const here = new URL(".", import.meta.url).pathname;
const css = readFileSync(join(here, "chat-feed.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

/** The declaration block of the rule whose selector is exactly `selector`. */
function rule(sheet: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = sheet.match(new RegExp(`(?:^|})\\s*${escaped}\\s*{([^}]*)}`));
  if (!m) throw new Error(`no rule for ${selector}`);
  return m[1];
}

describe("chat-feed.css", () => {
  it("skips laying out an entry that is off screen, and keeps a size for it", () => {
    const row = rule(css, ".chat-feed-row");
    expect(row).toMatch(/content-visibility:\s*auto/);
    expect(row).toMatch(/contain-intrinsic-size:\s*auto\s+\d+px/);
  });

  it("stands an answer in at an answer's height, not a one-line entry's", () => {
    // Measured on a 100-turn chat at 1440px: a question or a folded tool card
    // is 37–60px, an answer ~900px. One estimate for both would make every
    // never-drawn answer jump by hundreds of pixels when it is first drawn.
    const one = Number(rule(css, ".chat-feed-row").match(/auto\s+(\d+)px/)?.[1]);
    const answer = Number(rule(css, ".chat-feed-row--answer").match(/contain-intrinsic-size:\s*auto\s+(\d+)px/)?.[1]);
    expect(answer).toBeGreaterThan(one * 5);
  });

  it("is loaded by the app, not only present on disk", () => {
    const main = readFileSync(join(here, "..", "main.tsx"), "utf8");
    expect(main).toMatch(/import "\.\/styles\/chat-feed\.css";/);
  });
});
