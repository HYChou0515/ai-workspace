import { useEffect, useRef, useState } from "react";

import { type AgentLog, type TurnPhase, formatMetrics, isToolRunning, turnPhase } from "../pages/investigation/agentLog";
import { pxToRem } from "../lib/pxToRem";

/** The trailing status of an in-flight turn — replaces the opaque "working…"
 * spinner. It splits the wait into legible phases (see {@link turnPhase}) and
 * runs its OWN clock so the elapsed time keeps ticking even when the backend
 * emits nothing (a blocked event loop, a busy LLM service) — the one signal
 * that never freezes. Copy describes the action only, never system internals.
 *
 *  - prep:      準備中…            (backend hand-off; if it lingers → backend slow)
 *  - waiting:   等候模型回應…       (prompt is with the model, no token yet → LLM slow)
 *  - thinking:  思考中…            (reasoning is streaming; the content shows below)
 *  - answering / a running tool → defer to the existing ↑/↓ token metrics line. */
export function TurnStatus({ log, className }: { log: AgentLog; className?: string }) {
  const phase = turnPhase(log);
  const toolRunning = isToolRunning(log);
  const active = phase !== "idle";

  // Never-freeze clock: seconds since this turn started streaming. Anchored on
  // the FE (not the backend's elapsed_ms, which is 0 until the first token and
  // stalls if the server is wedged), so the timer ticks regardless of events.
  const startRef = useRef<number | null>(null);
  const [, tick] = useState(0);
  if (active && startRef.current === null) startRef.current = Date.now();
  if (!active) startRef.current = null;
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [active]);

  if (phase === "idle") return null;

  // Self-contained styling so both chat surfaces (KB + RCA) look identical —
  // mono, accent, indented to sit under the speaker avatar.
  const box = {
    fontFamily: "var(--font-mono)",
    fontSize: pxToRem(11),
    color: "var(--accent)",
    padding: "2px 0 0 28px",
  } as const;

  // The model has produced output and is answering (or is mid tool-call): the
  // existing token line (↑/↓ tok · tok/s, or "running…") is the right signal.
  if (phase === "answering" || toolRunning) {
    if (!log.metrics) return null;
    return (
      <div className={className} style={box}>
        {formatMetrics(log.metrics, toolRunning)}
      </div>
    );
  }

  const elapsedSec = startRef.current ? Math.floor((Date.now() - startRef.current) / 1000) : 0;
  return (
    <div className={className} style={box}>
      {statusText(phase, elapsedSec)}
      {elapsedSec >= 1 && <span style={{ opacity: 0.7 }}> · {elapsedSec}s</span>}
    </div>
  );
}

function statusText(phase: TurnPhase, sec: number): string {
  if (phase === "prep") return sec > 4 ? "還在準備,稍等一下" : "準備中…";
  if (phase === "thinking") return "思考中…";
  // waiting — the long blank gap; escalate honest reassurance with elapsed time
  // (never claim content volume or guess a cause; "busy" is true regardless).
  if (sec > 40) return "這次比較久,可隨時按 Stop 重試";
  if (sec > 15) return "模型忙碌中,請再稍候";
  return "等候模型回應…";
}
