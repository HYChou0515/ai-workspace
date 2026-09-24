/**
 * Which drawn points the spec's `highlight:` lights (P7).
 *
 * The sandbox resolves the highlight into a bitset over each layer's rows;
 * this maps it through `Built.series` to data indices per series. The chart
 * then dispatches ECharts' own `highlight` action for them: every series has
 * `emphasis.focus: "self"`, so the lit points take the emphasis state and the
 * rest blur — ECharts' states, not a second colour path.
 */
import type { Answer, Built } from "./option";
import { decodeBits } from "./wire";

export type HighlightTarget = { seriesIndex: number; dataIndex: number[] };

export function highlightTargets(built: Built, answer: Answer): HighlightTarget[] {
  const lit = answer.layers.map((ly) => (ly.highlight ? decodeBits(ly.highlight, ly.rows) : null));
  const out: HighlightTarget[] = [];
  built.series.forEach((s, seriesIndex) => {
    const bits = lit[s.layer];
    if (!bits) return;
    const dataIndex = s.rows.flatMap((row, i) => (bits[row] ? [i] : []));
    if (dataIndex.length) out.push({ seriesIndex, dataIndex });
  });
  return out;
}
