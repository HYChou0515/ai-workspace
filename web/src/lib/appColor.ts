/**
 * The palette for an App's identity pill.
 *
 * An App declares ONE colour in its `app.json` (`"color": "#F0502E"`), chosen to
 * look right as a 4px card accent on the launcher. Using that hex directly as
 * INK is what a pill invites and must not do: the declared colours run from
 * `#F0502E` to `#0EA5A4`, whose lightnesses differ by a factor a reader feels as
 * "one of these labels is unreadable". Teal on cream is ~2.8:1 — under every
 * floor — while the same teal on ink is fine, so the defect is invisible in
 * whichever theme you happen to be looking at (#690's lesson, in reverse).
 *
 * So the HUE is the identity and the LIGHTNESS is ours: the ink keeps the App's
 * hue and (gamut-permitting) its chroma, re-lit to a fixed lightness per theme.
 * That makes contrast a property of this palette rather than of whichever colour
 * an App happened to pick — the same trade `actorColor` makes for assignees.
 *
 * The fill stays translucent so ONE value serves both themes, the way the
 * `--cat-N-bg` chip fills do; over cream it reads as a wash of the App's colour,
 * over ink as a darker one.
 *
 * Everything is emitted as `rgba()` / hex, never `oklch()` — happy-dom drops an
 * `oklch()` inline style outright, and a colour guard that reads an empty string
 * passes on every element it can no longer measure.
 */

import { tintOf, toHex, toOklch } from "./oklch";

/** How much of the App's colour the pill's fill carries. Low: the pill labels a
 * row, it does not compete with the row's title. */
const TINT_ALPHA = 0.16;

/** Ink lightness per theme, verified against every shipped App colour in
 * `appColor.test.ts`. Two values because one cannot work: an ink dark enough to
 * read on cream is a hole on ink, and the tokens' own comment says the same of
 * the categorical chips. */
const INK_LIGHT = 0.46;
const INK_DARK = 0.83;

export type AppTagPalette = {
  /** Translucent fill, valid over either theme's surface. */
  tint: string;
  /** Ink for the light theme. */
  inkLight: string;
  /** Ink for the dark theme. */
  inkDark: string;
};

/**
 * `hex` is the App's declared colour. A malformed or missing one falls back to
 * a NEUTRAL pill rather than throwing: a manifest typo should cost the row its
 * colour, not the whole resources page — and a grey pill still names its App.
 */
export function appTagPalette(hex: string | undefined): AppTagPalette | null {
  if (!hex) return null;
  try {
    const { chroma, hue } = toOklch(hex);
    return {
      tint: tintOf(hex, TINT_ALPHA),
      inkLight: toHex(INK_LIGHT, chroma, hue),
      inkDark: toHex(INK_DARK, chroma, hue),
    };
  } catch {
    return null;
  }
}
