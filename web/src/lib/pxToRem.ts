/**
 * Express a pixel font size as `rem` relative to a 16px root (#226).
 *
 * Font sizes go through here so they scale with the user's system font-size
 * setting, which drives `:root { font-size }` (see hooks/fontScale.ts). At the
 * default 100% scale the root is 16px, so `pxToRem(14)` renders at the same
 * 14px it always did — the migration is pixel-identical until the user moves
 * the slider. Spacing/layout px stay px on purpose: keeping them fixed is what
 * preserves multi-pane layouts while only text grows.
 *
 * 16 is a power of two, so `n / 16` is exact in float64 for integer px — no
 * rounding artefacts in the emitted string.
 */
const ROOT_PX = 16;

export function pxToRem(px: number): string {
  if (px === 0) return "0";
  return `${px / ROOT_PX}rem`;
}
