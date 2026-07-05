import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { BREAKPOINTS } from "../lib/breakpoints";

/**
 * #464 — layout can't be measured in happy-dom, so the responsive rules are
 * guarded as a CSS drift check (same approach as tokens.test.ts). This asserts
 * the KB shells collapse to a single column at the shared narrow breakpoint;
 * the visual behaviour itself is verified with Playwright at 360/768/1024/1440.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const KB = readFileSync(resolve(HERE, "kb.css"), "utf8");

/** Brace-match the narrow @media block body so assertions can't leak into
 * unrelated rules elsewhere in the file. */
function narrowBlock(css: string): string {
  const m = /@media\s*\(max-width:\s*767px\)\s*\{/.exec(css);
  if (!m) return "";
  let depth = 0;
  const start = m.index + m[0].length - 1;
  for (let i = start; i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}") {
      depth--;
      if (depth === 0) return css.slice(start, i + 1);
    }
  }
  return "";
}

describe("kb.css narrow responsive (#464)", () => {
  const block = narrowBlock(KB);

  it("targets the shared narrow breakpoint (one px below breakpoints.narrow)", () => {
    expect(block).not.toBe("");
    expect(KB).toContain(`@media (max-width: ${BREAKPOINTS.narrow - 1}px)`);
  });

  it("collapses the KB home + chats grids to a single column", () => {
    expect(block).toMatch(/\.kb-shell\s*\{[^}]*grid-template-columns:\s*1fr/);
    expect(block).toMatch(/\.kb-chats-split\s*\{[^}]*grid-template-columns:\s*1fr/);
  });

  it("turns the nav rail into a horizontal strip", () => {
    expect(block).toMatch(/\.kb-nav\s*\{[^}]*flex-direction:\s*row/);
    expect(block).toMatch(/\.kb-nav\s*\{[^}]*overflow-x:\s*auto/);
  });

  it("stacks the doc IDE tree above the pane, overriding its inline width", () => {
    expect(block).toMatch(/\.kb-ide__main\s*\{[^}]*flex-direction:\s*column/);
    expect(block).toMatch(/\.kb-ide__tree\s*\{[^}]*width:\s*auto\s*!important/);
  });
});
