/**
 * sRGB ⇄ Oklch, and the one rule that makes a generated colour safe to put text
 * on: hold the LIGHTNESS and give up chroma when the gamut demands it.
 *
 * Extracted from `renderers/entity/actorColor.ts`, which had these matrices
 * privately and now imports them. A second copy is how the gantt's palette and
 * a pill's palette would drift into disagreeing about what "the same colour"
 * means, and the numbers are the kind nobody re-derives when they edit one side.
 *
 * Everything here resolves to a **hex or rgba string**, never an `oklch()`
 * literal: `oklch()` in an inline style is dropped outright by happy-dom, which
 * leaves a colour guard reading an empty string and passing on every element it
 * can no longer measure (`reference_happydom_drops_oklch`).
 */

/** Oklab → linear sRGB (Björn Ottosson's matrices). Private: `toHex` is the
 * only thing that should be reached for, and an exported helper with no caller
 * is public surface nothing guards. */
function linearRgb(lightness: number, a: number, b: number): [number, number, number] {
  const l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

const inGamut = (rgb: readonly number[]): boolean =>
  rgb.every((c) => c >= -1e-4 && c <= 1 + 1e-4);

/** Linear-light channel → sRGB, clamped. */
const gamma = (c: number): number => {
  const v = Math.min(1, Math.max(0, c));
  return v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
};

/** sRGB channel → linear-light. */
const ungamma = (c: number): number =>
  c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;

/** `#rgb` / `#rrggbb` → the three 0–255 channels. Throws on anything else, so a
 * malformed manifest colour fails where it is introduced rather than painting
 * an element black three surfaces away. */
export function parseHex(hex: string): [number, number, number] {
  const h = hex.trim().replace(/^#/, "");
  const full = h.length === 3 ? [...h].map((c) => c + c).join("") : h;
  if (!/^[0-9a-f]{6}$/i.test(full)) throw new Error(`not a hex colour: ${hex}`);
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)) as [number, number, number];
}

/** The hue angle (degrees) and chroma of a colour, so a declared brand hex can
 * be re-lit without losing which colour it IS. */
export function toOklch(hex: string): { lightness: number; chroma: number; hue: number } {
  const [r, g, b] = parseHex(hex).map((c) => ungamma(c / 255));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const lightness = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const bb = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  const hue = (Math.atan2(bb, a) * 180) / Math.PI;
  return { lightness, chroma: Math.hypot(a, bb), hue: hue < 0 ? hue + 360 : hue };
}

/**
 * The sRGB hex for one (lightness, chroma, hue), holding LIGHTNESS and giving up
 * chroma if that is what it takes to land inside the gamut — never the other way
 * round, because lightness is what the contrast floor rests on. Clipping the
 * CHANNELS instead would drag neighbouring hues onto the gamut boundary and
 * collapse them into each other.
 */
export function toHex(lightness: number, chroma: number, hue: number): string {
  const rad = (hue * Math.PI) / 180;
  const at = (c: number) => linearRgb(lightness, c * Math.cos(rad), c * Math.sin(rad));
  let lo = 0;
  let hi = chroma;
  if (!inGamut(at(hi))) {
    // 20 halvings resolve chroma far finer than 8-bit output can express.
    for (let i = 0; i < 20; i++) {
      const mid = (lo + hi) / 2;
      if (inGamut(at(mid))) lo = mid;
      else hi = mid;
    }
    hi = lo;
  }
  return `#${at(hi)
    .map((c) =>
      Math.round(gamma(c) * 255)
        .toString(16)
        .padStart(2, "0"),
    )
    .join("")}`;
}

/** A translucent fill of `hex`, as `rgba(...)`. Translucent rather than a
 * pre-mixed hex so ONE value works on both themes' surfaces — the same shape
 * the `--cat-N-bg` chip fills use. */
export function tintOf(hex: string, alpha: number): string {
  const [r, g, b] = parseHex(hex);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
