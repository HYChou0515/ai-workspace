import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { DARK, LIGHT, TOKENS_CSS, contrast, over, parseFill, tokenIn } from "../test/contrast";
import { appTagPalette } from "./appColor";
import { parseHex, toOklch } from "./oklch";

/**
 * The App pill's ink must be legible on the App pill's fill — for EVERY App,
 * in BOTH themes.
 *
 * Read off the shipped `app.json` files rather than a list written here. A
 * hardcoded list guards the Apps that existed the day it was typed; the next App
 * to declare a pale colour would ship a pill nobody can read, with a green suite.
 * That is the whole failure mode this file exists for — the declared colours
 * already span `#F0502E` and `#0EA5A4`, and raw teal ink on cream is ~2.8:1.
 */
const HERE = dirname(fileURLToPath(import.meta.url));
const APPS = resolve(HERE, "../../../src/workspace_app/apps");

function shippedAppColors(): { slug: string; color: string }[] {
  return readdirSync(APPS, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .flatMap((d) => {
      let raw: string;
      try {
        raw = readFileSync(join(APPS, d.name, "app.json"), "utf8");
      } catch {
        return []; // not an App directory (shared python modules live here too)
      }
      const m = JSON.parse(raw) as { slug?: string; color?: string };
      return m.color ? [{ slug: m.slug ?? d.name, color: m.color }] : [];
    });
}

describe("App pill palette", () => {
  const apps = shippedAppColors();

  it("finds the shipped Apps (a guard over an empty list proves nothing)", () => {
    // The positive control. If the path above ever stops resolving, every
    // `it.each` below silently becomes zero cases and the file goes green while
    // guarding nothing at all.
    expect(apps.length).toBeGreaterThanOrEqual(3);
    expect(apps.map((a) => a.slug)).toContain("rca");
  });

  // WCAG 1.4.11's 3:1 floor for a UI component / large-ish label, held for the
  // small bold text a pill carries. The fill is translucent, so what the ink
  // actually sits on is the fill composited over the theme's paper.
  const FLOOR = 3;

  for (const { slug, color } of apps) {
    it(`keeps ${slug} (${color}) legible in light mode`, () => {
      const p = appTagPalette(color);
      expect(p).not.toBeNull();
      const paper = tokenIn(TOKENS_CSS, LIGHT, "--paper");
      const surface = over(parseFill(p!.tint), paper);
      expect(contrast(p!.inkLight, surface)).toBeGreaterThanOrEqual(FLOOR);
    });

    it(`keeps ${slug} (${color}) legible in dark mode`, () => {
      const p = appTagPalette(color);
      const paper = tokenIn(TOKENS_CSS, DARK, "--paper");
      const surface = over(parseFill(p!.tint), paper);
      expect(contrast(p!.inkDark, surface)).toBeGreaterThanOrEqual(FLOOR);
    });

    it(`keeps ${slug} recognisable as its own colour`, () => {
      // Re-lighting must not become re-colouring: the ink has to stay the App's
      // hue, or "the pill is the App's colour" is false and two Apps could even
      // converge. 12° is well inside one colour name.
      const p = appTagPalette(color)!;
      const declared = toOklch(color).hue;
      for (const ink of [p.inkLight, p.inkDark]) {
        // Shortest distance around the hue circle. Written the long way because
        // the first version took the COMPLEMENT of it and reported every App as
        // 180° off its own colour — a guard that fails on correct output is
        // still a broken guard.
        const raw = toOklch(ink).hue - declared;
        const delta = Math.abs(((raw + 180) % 360) - 180);
        expect(delta).toBeLessThanOrEqual(12);
      }
    });
  }

  it("carries the App's own colour into the fill", () => {
    // The fill is the App's declared colour, not a re-lit one — it is what makes
    // the pill read as that App at a glance.
    const [r, g, b] = parseHex("#F0502E");
    expect(appTagPalette("#F0502E")!.tint).toContain(`${r}, ${g}, ${b}`);
  });

  it("gives a neutral pill rather than throwing on a malformed colour", () => {
    // A manifest typo should cost that row its colour, not take down the page.
    expect(appTagPalette("not-a-colour")).toBeNull();
    expect(appTagPalette(undefined)).toBeNull();
    expect(appTagPalette("")).toBeNull();
  });
});
