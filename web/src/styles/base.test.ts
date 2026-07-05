import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * a11y guard (#456): keyboard users need a visible focus indicator on every
 * focusable control. The reset strips the UA button border/background, so
 * without an explicit `:focus-visible` ring a Tab-through leaves nothing on
 * screen. This asserts base.css declares a global, branded ring — mouse clicks
 * don't match `:focus-visible`, so it only shows for keyboard / AT users.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE_PATH = resolve(HERE, "base.css");

describe("base.css focus-visible ring (#456)", () => {
  it("declares a global :focus-visible rule with a branded outline", () => {
    const css = readFileSync(BASE_PATH, "utf8");
    const block = /:focus-visible\s*\{[^}]*\}/.exec(css)?.[0] ?? "";
    expect(block).toMatch(/outline:[^;]*var\(--accent\)/);
    // A real ring, not a hairline: at least a 2px stroke, offset off the edge.
    expect(block).toMatch(/outline:[^;]*2px/);
    expect(block).toMatch(/outline-offset:/);
  });
});
