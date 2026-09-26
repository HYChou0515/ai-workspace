/**
 * Which rows the spec's `highlight:` lights (P7).
 *
 * The sandbox resolves the highlight into a bitset over each layer's rows.
 * `toOption` draws the unlit rows DIMMED in the data itself (item opacity; a
 * grid's cells at reduced alpha in its raster), the palette unchanged. Not
 * ECharts' emphasis / blur states: every highlight and downplay action — a
 * mouse passing over the chart included — begins with `allLeaveBlur`, so a
 * highlight kept there vanished at the first hover, and a second series'
 * highlight blurred the first's lit points (both measured on real ECharts).
 */
import type { WireLayer } from "./option";
import { decodeBits } from "./wire";

/** Item opacity of an unlit row — the same value hover's blur uses. */
export const DIM_OPACITY = 0.15;

/** Per row, whether it is lit; null when the layer has no highlight. */
export function litRows(layer: WireLayer): boolean[] | null {
  return layer.highlight ? decodeBits(layer.highlight, layer.rows) : null;
}
