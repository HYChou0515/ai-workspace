/**
 * Interactive Timeline / Gantt view of a run's steps (#283, complaint 3).
 *
 * A second view alongside the simple step board (kept as the default). It lays each
 * timed step out as a bar on an active-time-compressed axis (see lib/timeline) so you
 * can see where the run spent its time and how long it waited at a gate, then lets you
 * pan (drag) and zoom (buttons / wheel). While the run is live it follows "now"; pan or
 * zoom away and it stays put with a "jump to now" affordance.
 */

import { useEffect, useRef, useState } from "react";

import type { StepStateDTO } from "../api/workflows";
import { fmtElapsed } from "../api/workflows";
import { followOffset, timelineModel } from "../lib/timeline";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";

const ROW_H = 22;
const MIN_BAR_PX = 3;
const FALLBACK_W = 600;

function statusColor(status: string): string {
  switch (status) {
    case "passed":
      return "var(--ok, #2e7d32)";
    case "failed":
      return "var(--err)";
    case "retrying":
      return "var(--warn, #b26a00)";
    case "skipped":
      return "var(--text-paper-d2)";
    default: // running
      return "var(--accent, var(--info))";
  }
}

/** A ticking clock while `live`, so a running step's bar keeps growing toward now. */
function useNow(live: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [live]);
  return now;
}

export function WorkflowTimeline({ steps, live }: { steps: StepStateDTO[]; live: boolean }) {
  const t = useT();
  const now = useNow(live);
  const model = timelineModel(steps, now);

  const trackRef = useRef<HTMLDivElement>(null);
  const [widthPx, setWidthPx] = useState(FALLBACK_W);
  useEffect(() => {
    const el = trackRef.current;
    if (!el) return;
    const measure = () => setWidthPx(el.clientWidth || FALLBACK_W);
    measure();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
  }, []);

  // px-per-compressed-ms. Default fits the whole run to the track; zoom multiplies it.
  const fit = model.totalMs > 0 ? widthPx / model.totalMs : 1;
  const [zoom, setZoom] = useState(1);
  const pxPerMs = Math.max(fit * zoom, 1e-6);
  const viewportMs = widthPx / pxPerMs;

  const [following, setFollowing] = useState(true);
  const [panMs, setPanMs] = useState(0);
  // While following a live run, the right edge ("now") stays pinned; manual pan/zoom
  // drops out of follow and a "jump to now" button appears.
  const offsetMs = followOffset(model.totalMs, viewportMs, live && following, panMs);

  const stopFollow = () => {
    if (following) {
      setFollowing(false);
      setPanMs(offsetMs); // freeze where we are before manual control
    }
  };
  const jumpToNow = () => {
    setFollowing(true);
    setZoom(1);
  };

  // Drag-to-pan (pointer). Layout-dependent, so exercised manually / in e2e, not units.
  const drag = useRef<{ x: number; pan: number } | null>(null);
  const onPointerDown = (e: React.PointerEvent) => {
    stopFollow();
    drag.current = { x: e.clientX, pan: offsetMs };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    const dx = e.clientX - drag.current.x;
    setPanMs(Math.max(0, Math.min(drag.current.pan - dx / pxPerMs, Math.max(0, model.totalMs - viewportMs))));
  };
  const onPointerUp = () => {
    drag.current = null;
  };
  const onWheel = (e: React.WheelEvent) => {
    if (!e.ctrlKey && !e.shiftKey) return; // only zoom on a modifier so the page can scroll
    stopFollow();
    setZoom((z) => Math.max(0.2, Math.min(z * (e.deltaY < 0 ? 1.2 : 1 / 1.2), 40)));
  };

  const x = (ms: number) => (ms - offsetMs) * pxPerMs;
  const rows = model.bars.length;

  return (
    <div data-testid="wf-timeline" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <div style={{ flex: 1 }} />
        <button
          type="button"
          data-testid="wf-timeline-zoom-out"
          aria-label={t("wf.timeline.zoomOut")}
          onClick={() => {
            stopFollow();
            setZoom((z) => Math.max(0.2, z / 1.4));
          }}
          style={miniBtn}
        >
          −
        </button>
        <button
          type="button"
          data-testid="wf-timeline-zoom-in"
          aria-label={t("wf.timeline.zoomIn")}
          onClick={() => {
            stopFollow();
            setZoom((z) => Math.min(40, z * 1.4));
          }}
          style={miniBtn}
        >
          +
        </button>
        {live && !following && (
          <button type="button" data-testid="wf-timeline-now" onClick={jumpToNow} style={nowBtn}>
            {t("wf.timeline.now")}
          </button>
        )}
      </div>

      {rows === 0 ? (
        <p data-testid="wf-timeline-empty" style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
          {t("wf.timeline.empty")}
        </p>
      ) : (
        <div
          ref={trackRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onWheel={onWheel}
          style={{
            position: "relative",
            height: rows * ROW_H + 18,
            overflow: "hidden",
            background: "var(--paper-2)",
            borderRadius: 6,
            cursor: drag.current ? "grabbing" : "grab",
            touchAction: "none",
          }}
        >
          {model.gaps.map((g, i) => {
            const gx = x(g.x);
            if (gx < 0 || gx > widthPx) return null;
            return (
              <div
                key={`gap-${i}`}
                data-testid="wf-timeline-gap"
                style={{ position: "absolute", left: gx, top: 0, bottom: 0, pointerEvents: "none" }}
              >
                <div style={{ position: "absolute", top: 0, bottom: 18, width: 0, borderLeft: "1px dashed var(--text-paper-d2)" }} />
                <span
                  style={{
                    position: "absolute",
                    bottom: 1,
                    left: 3,
                    whiteSpace: "nowrap",
                    fontSize: pxToRem(9.5),
                    color: "var(--text-paper-d2)",
                  }}
                >
                  {t("wf.timeline.waited", { mins: Math.max(1, Math.round(g.realMs / 60000)) })}
                </span>
              </div>
            );
          })}

          {model.bars.map((b) => {
            const left = x(b.x0);
            const w = Math.max(MIN_BAR_PX, (b.x1 - b.x0) * pxPerMs);
            if (left + w < 0 || left > widthPx) return null;
            const elapsed = (b.step.ended ?? now) - (b.step.started ?? now);
            return (
              <div
                key={`${b.step.phase}:${b.step.name}:${b.step.key}`}
                data-testid="wf-timeline-bar"
                data-step={b.step.name}
                title={`${b.step.name} · ${fmtElapsed(Math.max(0, elapsed))}`}
                style={{
                  position: "absolute",
                  left,
                  top: b.row * ROW_H + 3,
                  width: w,
                  height: ROW_H - 6,
                  background: statusColor(b.step.status),
                  borderRadius: 3,
                  display: "flex",
                  alignItems: "center",
                  paddingLeft: 5,
                  overflow: "hidden",
                  opacity: b.step.status === "skipped" ? 0.5 : 1,
                }}
              >
                <span style={{ fontSize: pxToRem(10), color: "#fff", whiteSpace: "nowrap" }}>
                  {b.step.name}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const miniBtn: React.CSSProperties = {
  width: 22,
  height: 22,
  borderRadius: 6,
  border: "1px solid var(--paper-3)",
  background: "var(--white)",
  color: "var(--text-paper)",
  cursor: "pointer",
  lineHeight: 1,
};

const nowBtn: React.CSSProperties = {
  height: 22,
  padding: "0 10px",
  borderRadius: 6,
  border: "1px solid var(--accent, var(--info))",
  background: "var(--accent, var(--info))",
  color: "#fff",
  cursor: "pointer",
  fontSize: pxToRem(11),
};
