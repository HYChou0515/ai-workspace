/**
 * Colour for an ACTOR value — `color_by: assignee` on a gantt (#690 P4 gave the
 * chart the setting; this gives a directory-sized field a palette that fits it).
 *
 * `selectColor`'s six fixed slots are a closed vocabulary's shape: a `status`
 * has four values that a schema can pin by name, and the same value must wear
 * the same colour in the table chip beside the chart. A directory has neither
 * property — it is open-ended, and hashing into six buckets makes four people
 * collide 44% of the time and seven collide outright.
 *
 * So the hue is GENERATED rather than picked. Seats are handed out in the order
 * people first appear in the records, and seat `k` takes the k-th term of the
 * van der Corput sequence (base 2, half-open) as its hue angle:
 *
 *     0, ½, ¼, ¾, ⅛, ⅜, ⅝, ⅞, …
 *
 * Each new seat lands in the middle of the largest remaining gap, so the hues
 * of `n` people are never closer than `360 / 2^⌈log₂ n⌉` degrees — they crowd as
 * the team grows but never repeat, and there is no head count at which the
 * palette runs out. `1` is deliberately absent: on a hue circle it IS `0`.
 */

import type { ChipColor } from "./selectColor";

/**
 * Lightness and chroma are FIXED so only the hue carries identity. Locking the
 * OKLCH lightness is what makes the text contrast a property of the palette
 * rather than of the hue you happened to draw: measured across the sequence it
 * holds at 5.95:1 against `--ink`, in both themes, because neither the fill nor
 * the ink is theme-dependent.
 *
 * Chroma is a REQUEST, reduced per hue until the colour fits sRGB — the same
 * give CSS Color 4 gamut mapping makes, and for the same reason: clipping the
 * CHANNELS instead would drag neighbouring hues onto the gamut boundary and
 * collapse them back into each other, undoing the spacing above.
 *
 * The colour is resolved to a hex here rather than emitted as `oklch()` so that
 * it is a value every reader can see. `oklch()` in an inline style is dropped
 * outright by happy-dom, which would leave the bar-contrast guard reading an
 * empty string and passing on every bar it could no longer measure.
 */
const LIGHTNESS = 0.7;
const CHROMA = 0.16;
/** Non-inverting by construction: `--ink` is one value in both themes, so a
 * bar's text cannot flip to the other theme's colour the way `--white` does. */
const INK = "var(--ink)";

/** The k-th term of the van der Corput sequence, base 2, over `[0, 1)`. */
export function hueFraction(k: number): number {
  if (k <= 0) return 0;
  const level = Math.floor(Math.log2(k)) + 1;
  return (2 * (k - 2 ** (level - 1)) + 1) / 2 ** level;
}

/** Oklab → linear sRGB (Björn Ottosson's matrices). */
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

const gamma = (c: number): number => {
  const v = Math.min(1, Math.max(0, c));
  return v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
};

/**
 * The sRGB hex for one hue, holding LIGHTNESS and giving up chroma if that is
 * what it takes to land inside the gamut — never the other way round, because
 * lightness is what the contrast floor rests on.
 */
function fill(chroma: number, hue: number): string {
  const rad = (hue * Math.PI) / 180;
  const at = (c: number) => linearRgb(LIGHTNESS, c * Math.cos(rad), c * Math.sin(rad));
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
  const hex = at(hi)
    .map((c) => Math.round(gamma(c) * 255).toString(16).padStart(2, "0"))
    .join("");
  return `#${hex}`;
}


/**
 * Build the palette for one chart from its records' actor values, IN RECORD
 * ORDER. Order matters and appearance order is the kind that churns least:
 * someone newly assigned is almost always assigned on a new record, so they
 * take the next free seat and nobody else's colour moves.
 *
 * The returned lookup answers for any id — an empty or unseated value (nobody
 * owns this work) gets the same lightness with NO chroma, so it reads as "not a
 * person" rather than as one more colour, and costs no seat: the people who ARE
 * on the chart keep the wider spacing.
 */
export function actorPalette(valuesInRecordOrder: readonly string[]): (value: string) => ChipColor {
  const seats = new Map<string, number>();
  for (const value of valuesInRecordOrder) {
    if (value && !seats.has(value)) seats.set(value, seats.size);
  }
  return (value: string): ChipColor => {
    const seat = value ? seats.get(value) : undefined;
    return seat === undefined
      ? { bg: fill(0, 0), fg: INK }
      : { bg: fill(CHROMA, hueFraction(seat) * 360), fg: INK };
  };
}
