/**
 * The video's size, three ways (plan-chat-video-export decision 8): the
 * dialog always SHOWS width × height and the text scale, and lets the person
 * arrive at them the way that is natural for them —
 *
 * - `resolution`: an aspect and a short side ("1080p"), the text scale then
 *   being the player's automatic one;
 * - `text`: an aspect and how big the text should be — the frame follows,
 *   and the scale is pinned so that promise holds for every aspect;
 * - `custom`: a width and a height typed in, and a text size if wanted.
 *
 * All three are equivalent as a request; they differ only in what a person
 * finds easy to say. The result is what goes into `VideoOptions`. The
 * aspects and the text sizes are the plan's table (尺寸的三種輸入法).
 */

export type Aspect = "16:9" | "1:1" | "9:16";
export const ASPECTS: Aspect[] = ["16:9", "1:1", "9:16"];

/** The short side, the way frames are named: 480p … 2160p. The last two are
 * past the default ceiling (`chat_video.max_pixels` = 1920×1080); the dialog
 * hides the steps the deployment does not allow. */
export const RESOLUTION_STEPS = [480, 720, 1080, 1440, 2160];

/** Text sizes relative to the 720p layout: 小 / 中 / 大 / 特大. */
export const TEXT_SIZES = [0.8, 1, 1.3, 1.6];

export type SizeChoice =
  | { mode: "resolution"; aspect: Aspect; p: number }
  | { mode: "text"; aspect: Aspect; textScale: number }
  | { mode: "custom"; width: number; height: number; textScale?: number };

export type ResolvedSize = {
  width: number;
  height: number;
  /** The `VideoOptions.scale` the video will render at. */
  scale: number;
  /** True when the player computes it (`VideoOptions.scale = 0`), false when
   * the request pins it. */
  scaleIsAuto: boolean;
};

const RATIO: Record<Aspect, [number, number]> = {
  "16:9": [16, 9],
  "1:1": [1, 1],
  "9:16": [9, 16],
};

/** The recorder rounds an odd side down to even before the encoder sees it
 * (`VideoOptions` says why the server no longer refuses one); rounding here
 * too keeps the size the result line SHOWS the size the file will have. */
function even(n: number): number {
  return Math.max(16, Math.floor(n / 2) * 2);
}

/** `player.ui_scale`'s automatic rule, mirrored: the frame relative to
 * 1280×720, floored at 1 — 1080p renders at 1.5×, 4K at 3×, a square or a
 * small frame at 1. */
export function autoUiScale(width: number, height: number): number {
  return Math.max(1, Math.min(width / 1280, height / 720));
}

/** The frame with this aspect whose SHORT side is `p`. */
export function frameFor(aspect: Aspect, p: number): { width: number; height: number } {
  const [w, h] = RATIO[aspect];
  const short = even(p);
  if (w >= h) return { width: even((short * w) / h), height: short };
  return { width: short, height: even((short * h) / w) };
}

export function resolveSize(choice: SizeChoice): ResolvedSize {
  if (choice.mode === "resolution") {
    const f = frameFor(choice.aspect, choice.p);
    return { ...f, scale: autoUiScale(f.width, f.height), scaleIsAuto: true };
  }
  if (choice.mode === "text") {
    // The 720p layout of this aspect, drawn `textScale` times larger.
    const f = frameFor(choice.aspect, 720 * choice.textScale);
    return { ...f, scale: choice.textScale, scaleIsAuto: false };
  }
  const width = even(choice.width);
  const height = even(choice.height);
  if (choice.textScale !== undefined)
    return { width, height, scale: choice.textScale, scaleIsAuto: false };
  return { width, height, scale: autoUiScale(width, height), scaleIsAuto: true };
}

/**
 * About how big the file will be, for the result line — from the two
 * measured points in `docs/chat-video.md` (the 41-second sample): 720p mp4
 * 1.8 MB / gif 18 MB, 1080p mp4 2.5 MB / gif 36 MB. Linear in length;
 * in pixels a power fitted to those two points (mp4 grows slowly with
 * pixels, gif nearly linearly). webm is the recording itself, 4.6 MB at
 * 1080p, given mp4's pixel curve. An estimate — the result line says
 * "約", never "最多": a 21 s 1080p mp4 of the demo chat came out 63% over
 * this figure — and the ceiling that matters (`chat_video.max_output_bytes`)
 * is the server's.
 */
const PER_SECOND_AT_720P: Record<string, { mb: number; pixelPower: number }> = {
  mp4: { mb: 1.8 / 41, pixelPower: Math.log(2.5 / 1.8) / Math.log(2.25) },
  gif: { mb: 18 / 41, pixelPower: Math.log(36 / 18) / Math.log(2.25) },
  webm: { mb: 4.6 / 41 / 2.25 ** (Math.log(2.5 / 1.8) / Math.log(2.25)), pixelPower: Math.log(2.5 / 1.8) / Math.log(2.25) },
};

export function estimateMegabytes(fmt: string, pixels: number, seconds: number): number {
  const k = PER_SECOND_AT_720P[fmt] ?? PER_SECOND_AT_720P.mp4;
  return seconds * k.mb * (pixels / (1280 * 720)) ** k.pixelPower;
}
