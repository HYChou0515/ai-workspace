/**
 * Pure layout model for the workflow Timeline / Gantt view (#283).
 *
 * The run's per-step board carries server epoch-ms `started`/`ended` (reload-safe);
 * the timeline lays those out as bars on a time axis. Two things make it readable:
 *
 *  - **Active-time compression** — a run can sit `awaiting_human` for minutes-to-hours;
 *    drawn to scale that idle stretch would squash every real step into a hairline. So
 *    we draw only the time some step was actually running (the union of step intervals)
 *    and replace each idle gap with a fixed-width break marker carrying the real waited
 *    duration (the "waited Nm" label).
 *  - **Compressed coordinates in ms** — bars/gaps are positioned on a synthetic ms axis
 *    (idle time removed). The component scales that to px (zoom) and offsets it (pan);
 *    keeping the model in ms keeps it pure + unit-testable, free of layout/DOM.
 */

import type { StepStateDTO } from "../api/workflows";

/** Fixed compressed width (ms) a skipped idle stretch occupies — the break marker. */
export const GAP_MS = 3000;

export type TimelineBar = {
  step: StepStateDTO;
  row: number; // y index (one row per timed step, in start order)
  x0: number; // compressed-ms start
  x1: number; // compressed-ms end (>= x0; a still-running step ends at `now`)
};

export type TimelineGap = {
  x: number; // compressed-ms position of the break marker
  realMs: number; // the real idle duration skipped here (for the "waited Nm" label)
};

export type TimelineModel = {
  bars: TimelineBar[];
  gaps: TimelineGap[];
  totalMs: number; // total compressed width (ms), idle time removed
};

/**
 * Build the compressed-axis layout for `steps` as of `now`. Only steps that recorded a
 * `started` appear (a cache-skip never started, so it has no bar). Overlapping steps
 * (genuine parallelism) share covered time; the idle gaps between covered stretches are
 * compressed to {@link GAP_MS} and reported as `gaps` with their real skipped duration.
 */
export function timelineModel(steps: StepStateDTO[], now: number): TimelineModel {
  const timed = steps
    .filter((s) => s.started != null)
    .map((s) => {
      const a = s.started as number;
      const b = Math.max(a, s.ended ?? now); // running → ends at now; guard b >= a
      return { s, a, b };
    })
    .sort((p, q) => p.a - q.a || p.b - q.b);

  if (timed.length === 0) return { bars: [], gaps: [], totalMs: 0 };

  // Merge overlapping/adjacent step intervals into the disjoint covered stretches.
  const covered: { a: number; b: number }[] = [];
  for (const { a, b } of timed) {
    const last = covered[covered.length - 1];
    if (last && a <= last.b) last.b = Math.max(last.b, b);
    else covered.push({ a, b });
  }

  // Map a real epoch-ms (always inside a covered stretch for a real bar) to its
  // compressed position: cumulative covered duration + GAP_MS per stretch boundary.
  const mapTime = (t: number): number => {
    let acc = 0;
    for (const c of covered) {
      if (t <= c.b) return acc + Math.max(0, t - c.a);
      acc += c.b - c.a + GAP_MS;
    }
    return acc;
  };

  const bars: TimelineBar[] = timed.map(({ s, a, b }, row) => ({
    step: s,
    row,
    x0: mapTime(a),
    x1: mapTime(b),
  }));

  const gaps: TimelineGap[] = [];
  for (let i = 0; i < covered.length - 1; i++) {
    const realMs = covered[i + 1].a - covered[i].b;
    if (realMs > 0) gaps.push({ x: mapTime(covered[i].b), realMs });
  }

  return { bars, gaps, totalMs: mapTime(covered[covered.length - 1].b) };
}

/**
 * Live-tail follow (#283): while a run is live the view auto-scrolls to keep "now" in
 * frame — UNLESS the operator has panned/zoomed away (then it stays put and a "jump to
 * now" affordance appears). `following` is the current follow state; this returns the
 * pan offset (compressed-ms at the left edge) to use, given the viewport width in ms.
 */
export function followOffset(totalMs: number, viewportMs: number, following: boolean, panMs: number): number {
  if (!following) return Math.max(0, Math.min(panMs, Math.max(0, totalMs - viewportMs)));
  return Math.max(0, totalMs - viewportMs); // pin the right edge ("now") in view
}
