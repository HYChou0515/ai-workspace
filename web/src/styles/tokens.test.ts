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

  it("declares the type scale (display-xl through xs and mono-caps)", () => {
    const css = readTokens();
    // README: display-xl 56/1.05, display-lg 40/1.10, display-md 28/1.15,
    // display-sm 22/1.20, body-lg 18/1.55, body 14/1.55, body-sm 13/1.5,
    // small 12/1.5, xs 11/1.5, mono-caps 11 (uppercase, ls 0.12em)
    expect(css).toMatch(/--text-display-xl:\s*56px/);
    expect(css).toMatch(/--leading-display-xl:\s*1\.05/);
    expect(css).toMatch(/--text-display-lg:\s*40px/);
    expect(css).toMatch(/--leading-display-lg:\s*1\.10?/);
    expect(css).toMatch(/--text-display-md:\s*28px/);
    expect(css).toMatch(/--leading-display-md:\s*1\.15/);
    expect(css).toMatch(/--text-display-sm:\s*22px/);
    expect(css).toMatch(/--leading-display-sm:\s*1\.20?/);
    expect(css).toMatch(/--text-body-lg:\s*18px/);
    expect(css).toMatch(/--text-body:\s*14px/);
    expect(css).toMatch(/--leading-body:\s*1\.55/);
    expect(css).toMatch(/--text-body-sm:\s*13px/);
    expect(css).toMatch(/--text-small:\s*12px/);
    expect(css).toMatch(/--text-xs:\s*11px/);
    expect(css).toMatch(/--text-mono-caps:\s*11px/);
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
