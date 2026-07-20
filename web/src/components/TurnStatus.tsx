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
export function TurnStatus({
  log,
  className,
  onRetry,
}: {
  log: AgentLog;
  className?: string;
  /** Ask the same question again, abandoning the stalled attempt. Omitted when
   * the running turn is not this viewer's to restart. */
  onRetry?: () => void;
}) {
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

  const elapsedSec = startRef.current ? Math.floor((Date.now() - startRef.current) / 1000) : 0;

  // Past 40s the copy stops changing and the counter just climbs — past a
  // minute, past an hour. The only exits were Stop (abandon the turn) and a new
  // chat (abandon the thread), and a wait that cannot be acted on is exactly the
  // state a user reads as "it's broken". So a long one offers the obvious
  // action. Absent `onRetry` there is nothing to offer — someone else's turn is
  // not yours to restart.
  // A send can be rejected by a gateway BEFORE it reaches the app: no turn runs,
  // nothing is persisted, and no terminal event can ever arrive — so the
  // deliberate "stay streaming, the turn may be running" tolerance waits
  // forever. Past the point where any real turn would have produced SOMETHING,
  // stop claiming to be waiting and say so. Visible output means the turn is
  // real; length alone is never the reason.
  // "No sign of life" is the real test, not the phase: a delta can arrive before
  // any metrics do, which still reads as `prep`. Any assistant text or tool call
  // means the turn is real and running.
  const producedSomething = log.entries.some(
    (e) =>
      e.kind === "tool_call" ||
      (e.kind === "message" && e.message.role === "assistant" && !!e.message.content),
  );
  if (phase === "prep" && !producedSomething && elapsedSec >= ABANDONED_AFTER_S) {
    return (
      <div className={className} style={box} data-testid="turn-abandoned">
        這一輪似乎沒有開始 — 可能在送出時就中斷了。
        {onRetry && (
          <button type="button" data-testid="turn-retry" onClick={onRetry} style={retryBtn}>
            重新問一次
          </button>
        )}
      </div>
    );
  }

  const retry =
    onRetry && elapsedSec >= RETRY_AFTER_S ? (
      <button
        type="button"
        data-testid="turn-retry"
        onClick={onRetry}
        style={retryBtn}
      >
        重新問一次
      </button>
    ) : null;

  // #492 P11: a cold sandbox is being restored from its durable snapshot before
  // the turn can run — show "還原工作區… N/M" instead of a blank running card.
  // Takes precedence over the tool-running metrics line below because the restore
  // happens INSIDE the first tool call's lazy wake (so a tool is "running").
  if (log.restore != null) {
    return (
      <div className={className} style={box}>
        還原工作區… {log.restore.done}/{log.restore.total}
        {elapsedSec >= 1 && <span style={{ opacity: 0.7 }}> · {elapsedSec}s</span>}
      </div>
    );
  }

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

  // #249/#131: while we wait on the model and it just failed over, say so —
  // a transient, de-jargoned reassurance (never the raw model id).
  const switched = phase === "waiting" && log.failover != null;
  return (
    <div className={className} style={box}>
      {switched ? "模型忙線,已自動切換,稍候…" : statusText(phase, elapsedSec)}
      {elapsedSec >= 1 && <span style={{ opacity: 0.7 }}> · {elapsedSec}s</span>}
      {retry}
    </div>
  );
}

const RETRY_AFTER_S = 60;
// Past this with no sign of life at all, a turn is not slow — it never began.
const ABANDONED_AFTER_S = 10 * 60;

const retryBtn: React.CSSProperties = {
  marginLeft: 8,
  padding: "0 6px",
  border: "1px solid var(--accent)",
  borderRadius: "var(--radius-btn)",
  background: "transparent",
  color: "var(--accent)",
  font: "inherit",
  cursor: "pointer",
};

function statusText(phase: TurnPhase, sec: number): string {
  if (phase === "prep") return sec > 4 ? "還在準備,稍等一下" : "準備中…";
  if (phase === "thinking") return "思考中…";
  // waiting — the long blank gap; escalate honest reassurance with elapsed time
  // (never claim content volume or guess a cause; "busy" is true regardless).
  if (sec > 40) return "這次比較久,可隨時按 Stop 重試";
  if (sec > 15) return "模型忙碌中,請再稍候";
  return "等候模型回應…";
}
