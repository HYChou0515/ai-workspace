/**
 * How big an attachment tile is, in px (#730).
 *
 * A card's attachments are not one kind of thing looked at one way: a defect
 * library wants photographs big enough to tell apart, and a card with twenty
 * links wants them small enough to see at once. Picking one number serves
 * neither, so the reader picks — and their choice sticks, because re-picking it
 * on every card would be worse than a wrong default.
 *
 * The value is the tile's WIDTH. It was briefly a `minmax()` floor with `1fr`
 * columns, which sounds more responsive and is worse: the rendered width then
 * equals container ÷ column count, an integer, so the slider did nothing until
 * the count changed and then jumped. Tiles are the size asked for and wrap when
 * the row runs out — the behaviour the control looks like it promises.
 *
 * Per-device (localStorage), like the font scale it borrows its shape from —
 * this is how one person likes to look at things, not a property of the card.
 */

import { useCallback, useEffect, useState } from "react";

const KEY = "ui:kb-attachment-tile";

/** Small enough that twenty links fit without scrolling. */
export const TILE_MIN = 84;
/** Big enough to read an annotation without opening the document. */
export const TILE_MAX = 320;
export const TILE_DEFAULT = 132;
export const TILE_STEP = 4;

/** The three sizes worth one click.
 *
 * A slider alone makes "back to normal" a hunt, and most people want one of a
 * few sizes rather than a specific pixel: small enough to scan a long list,
 * the default, or big enough to read an annotation in place. The slider stays
 * for everything in between. */
export const TILE_PRESETS: ReadonlyArray<{ label: string; px: number; title: string }> = [
  { label: "S", px: 96, title: "Small — scan a long list" },
  { label: "M", px: TILE_DEFAULT, title: "Medium" },
  { label: "L", px: 224, title: "Large — read an annotation in place" },
];

export function clampTileSize(px: number): number {
  if (!Number.isFinite(px)) return TILE_DEFAULT;
  return Math.min(TILE_MAX, Math.max(TILE_MIN, Math.round(px)));
}

export function readTileSize(): number {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw == null) return TILE_DEFAULT;
    return clampTileSize(Number.parseInt(raw, 10));
  } catch {
    // A browser with site data blocked still gets a working grid.
    return TILE_DEFAULT;
  }
}

/** The tile size and a setter that persists it. */
export function useAttachmentTileSize(): [number, (px: number) => void] {
  const [size, setSize] = useState<number>(readTileSize);

  useEffect(() => {
    try {
      localStorage.setItem(KEY, String(size));
    } catch {
      // Not being able to REMEMBER the choice must not stop it applying now.
    }
  }, [size]);

  const set = useCallback((px: number) => setSize(clampTileSize(px)), []);
  return [size, set];
}
