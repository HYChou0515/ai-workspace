import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const HERE = dirname(fileURLToPath(import.meta.url));
const TOKENS_PATH = resolve(HERE, "tokens.css");

function readTokens(): string {
  return readFileSync(TOKENS_PATH, "utf8");
}

describe("tokens.css drift guard", () => {
  it("declares the brand accent palette", () => {
    const css = readTokens();
    expect(css).toMatch(/--accent:\s*#F0502E/);
    expect(css).toMatch(/--accent-h:\s*#D8431F/);
    expect(css).toMatch(/--accent-soft:\s*#FCE4DC/);
  });

  it("declares the ink (dark) surface palette", () => {
    const css = readTokens();
    expect(css).toMatch(/--ink:\s*#16181D/);
    expect(css).toMatch(/--ink-2:\s*#1A1B1F/);
    expect(css).toMatch(/--ink-3:\s*#23262E/);
    expect(css).toMatch(/--ink-4:\s*#2E323B/);
  });

  it("declares the paper (light) surface palette", () => {
    const css = readTokens();
    expect(css).toMatch(/--paper:\s*#F1ECE0/);
    expect(css).toMatch(/--paper-2:\s*#E5E0D2/);
    expect(css).toMatch(/--paper-3:\s*#D8D2C2/);
    expect(css).toMatch(/--white:\s*#FBF9F4/);
  });

  it("declares the text-on-paper and text-on-dark palettes", () => {
    const css = readTokens();
    expect(css).toMatch(/--text-paper:\s*#1A1B1F/);
    expect(css).toMatch(/--text-paper-d:\s*#5C5F66/);
    expect(css).toMatch(/--text-paper-d2:\s*#8A8C90/);
    expect(css).toMatch(/--text-dark:\s*#F1ECE0/);
    expect(css).toMatch(/--text-dark-d:\s*#9CA0AB/);
  });

  it("declares a theme-flipping brand mark stroke (light + dark)", () => {
    const css = readTokens();
    expect(css).toMatch(/--brand-mark:\s*#1A1B1F/); // light: dark stroke
    expect(css).toMatch(/--brand-mark:\s*#F1ECE0/); // dark: cream stroke
  });

  it("declares the semantic (ok / warn / err / info) palette", () => {
    const css = readTokens();
    expect(css).toMatch(/--ok:\s*#3A8A4A/);
    expect(css).toMatch(/--warn:\s*#C68A2E/);
    expect(css).toMatch(/--err:\s*#C44A3A/);
    expect(css).toMatch(/--info:\s*#2D6CC9/);
  });

  it("declares the three font families (display / body / mono)", () => {
    const css = readTokens();
    // Display = Inter Tight, body = Inter, mono = JetBrains Mono
    expect(css).toMatch(/--font-display:[^;]*Inter Tight/);
    expect(css).toMatch(/--font-body:[^;]*\bInter\b/);
    expect(css).toMatch(/--font-mono:[^;]*JetBrains Mono/);
  });

  it("declares the type scale in rem so it scales with the system font size (#226)", () => {
    const css = readTokens();
    // Sizes are rem (n/16) so the font-size setting (:root font-size %) scales
    // them; px-equivalent at the default 100%: display-xl 56, display-lg 40,
    // display-md 28, display-sm 22, body-lg 18, body 14, body-sm 13, small 12,
    // xs 11, mono-caps 11. Leadings stay unit-less, tracking stays em.
    expect(css).toMatch(/--text-display-xl:\s*3\.5rem/);
    expect(css).toMatch(/--leading-display-xl:\s*1\.05/);
    expect(css).toMatch(/--text-display-lg:\s*2\.5rem/);
    expect(css).toMatch(/--leading-display-lg:\s*1\.10?/);
    expect(css).toMatch(/--text-display-md:\s*1\.75rem/);
    expect(css).toMatch(/--leading-display-md:\s*1\.15/);
    expect(css).toMatch(/--text-display-sm:\s*1\.375rem/);
    expect(css).toMatch(/--leading-display-sm:\s*1\.20?/);
    expect(css).toMatch(/--text-body-lg:\s*1\.125rem/);
    expect(css).toMatch(/--text-body:\s*0\.875rem/);
    expect(css).toMatch(/--leading-body:\s*1\.55/);
    expect(css).toMatch(/--text-body-sm:\s*0\.8125rem/);
    expect(css).toMatch(/--text-small:\s*0\.75rem/);
    expect(css).toMatch(/--text-xs:\s*0\.6875rem/);
    expect(css).toMatch(/--text-mono-caps:\s*0\.6875rem/);
    expect(css).toMatch(/--tracking-mono-caps:\s*0\.12em/);
  });

  it("declares the 4px spacing scale (4 / 8 / 12 / 16 / 24 / 32 / 48 / 64)", () => {
    const css = readTokens();
    for (const n of [4, 8, 12, 16, 24, 32, 48, 64]) {
      expect(css).toMatch(new RegExp(`--space-${n}:\\s*${n}px`));
    }
  });

  it("declares the radii (chip / btn / card / modal / avatar)", () => {
    const css = readTokens();
    // README: 4 (chip), 6 (btn / input), 8 (card), 12 (modal), 50% (avatar)
    expect(css).toMatch(/--radius-chip:\s*4px/);
    expect(css).toMatch(/--radius-btn:\s*6px/);
    expect(css).toMatch(/--radius-card:\s*8px/);
    expect(css).toMatch(/--radius-modal:\s*12px/);
    expect(css).toMatch(/--radius-avatar:\s*50%/);
  });
});
