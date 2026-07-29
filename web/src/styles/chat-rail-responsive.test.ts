import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { BREAKPOINTS } from "../lib/breakpoints";

/**
 * #fe-responsive — layout can't be measured in happy-dom, so the rail's narrow
 * rules are guarded as a CSS drift check (same approach as
 * `kb-responsive.test.ts`). The visual behaviour is verified with Playwright at
 * 390 / 768 / 1024 / 1440.
 *
 * Tucking the rail by default (ChatListRail) is only half the fix: if the user
 * opens it on a 390px viewport, a 240px in-flow column still leaves 150px for
 * the entire chat. On narrow it therefore overlays the chat instead of taking
 * a bite out of it — the same treatment the shell's file-tree sidebar gets.
 */
const HERE = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(resolve(HERE, "chat-rail.css"), "utf8");

function narrowBlock(css: string): string {
  const m = new RegExp(`@media\\s*\\(max-width:\\s*${BREAKPOINTS.narrow - 1}px\\)\\s*\\{`).exec(css);
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

describe("chat-rail.css narrow responsive (#fe-responsive)", () => {
  const block = narrowBlock(CSS);

  it("targets the shared narrow breakpoint (one px below breakpoints.narrow)", () => {
    expect(block).not.toBe("");
    expect(CSS).toContain(`@media (max-width: ${BREAKPOINTS.narrow - 1}px)`);
  });

  it("floats an OPEN rail over the chat instead of taking width from it", () => {
    expect(block).toMatch(/\.chat-rail\s*\{[^}]*position:\s*absolute/);
  });

  it("leaves the tucked rail in flow, so the thin bar is still a visible affordance", () => {
    expect(block).toMatch(/\.chat-rail--collapsed\s*\{[^}]*position:\s*relative/);
  });

  it("gives the overlay a positioning context on the workspace row", () => {
    expect(CSS).toMatch(/\.chat-workspace\s*\{[^}]*position:\s*relative/);
  });
});
