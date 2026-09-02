import { useEffect, useRef, useState } from "react";

import {
  type AgentLog,
  type TurnPhase,
  formatCounts,
  formatMetrics,
  isToolRunning,
  turnLooksSilent,
  turnPhase,
} from "../pages/investigation/agentLog";
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
/** #739: how long a compaction may claim to be running before we stop
 * believing it. Generous — a large span on a slow model is legitimately
 * minutes — but finite, because the alternative is a line nobody can clear. */
const COMPACTING_GIVE_UP_S = 300;

export function TurnStatus({
  log,
  className,
  onRetry,
  alive,
}: {
  log: AgentLog;
  className?: string;
  /** Ask the same question again, abandoning the stalled attempt. Omitted when
   * the running turn is not this viewer's to restart. */
  onRetry?: () => void;
  /** Whether any pod is driving this turn, asked of the server while the silence
   * lasts. `null`/absent = nobody could tell us. Today that lands on the same
   * screen as `false`, and the separate value exists so it cannot quietly become
   * "alive" later: being unable to ask is not evidence of life, and reading it as
   * one restores the endless wait this exists to end. */
  alive?: boolean | null;
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

  // #739: the compaction runs BEFORE the turn and gets its own clock. Folding
  // it into the turn's would lend the turn its elapsed time whenever the two
  // events land in one render — a two-second-old turn presenting as ten minutes
  // stuck, offering a retry button whose first act is to cancel it.
  const compacting = log.compacting != null;
  const compactStartRef = useRef<number | null>(null);
  if (compacting && compactStartRef.current === null) compactStartRef.current = Date.now();
  if (!compacting) compactStartRef.current = null;
  const compactSec = compactStartRef.current
    ? Math.floor((Date.now() - compactStartRef.current) / 1000)
    : 0;
  // The bound binds only where nothing else can clear it. A turn always ends
  // in a terminal event and every one of them clears `compacting`, so on the
  // automatic path there is nothing to protect against — and applying it there
  // dropped the status to 「還在準備」 (false: a compaction is running) and
  // restored the retry button, whose premise is "this is stuck" and whose first
  // act is to cancel. The manual path publishes no turn, so there a lost `done`
  // really would pin the line for every spectator with no way out.
  const compactingLive = compacting && (active || compactSec < COMPACTING_GIVE_UP_S);

  useEffect(() => {
    if (!active && !compactingLive) return;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [active, compactingLive]);

  // Self-contained styling so both chat surfaces (KB + RCA) look identical —
  // mono, accent, indented to sit under the speaker avatar.
  const box = {
    fontFamily: "var(--font-mono)",
    fontSize: pxToRem(11),
    color: "var(--accent)",
    padding: "2px 0 0 28px",
  } as const;

  // #739: an idle chat still has something to say while a summary is being
  // written — the manual path (button, `/compact`) never sets `streaming`, so
  // the notice would otherwise be published and dropped, and nobody but the
  // presser would know the thread was being rewritten.
  if (phase === "idle" && !compactingLive) return null;

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
  //
  // What it says is the EVIDENCE, not a verdict. "This turn never started" is a
  // claim about the server that this screen cannot make: a page reloaded during
  // a long tool call has no metrics either (the stream does not replay them), so
  // it looks identical to a send that was cut. The silence is what we can prove,
  // and the retry is offered on that.
  //
  // `turnLooksSilent` is deliberately the SAME predicate the panel asks under —
  // the question and the answer have to be about one condition — and it is what
  // scopes the check to THIS turn. Asked of the whole log, "has it produced
  // anything" is yes forever in any chat ever answered, which is why this notice
  // could not appear at all and 「還在準備,稍等一下」 was still on screen at
  // 1300 seconds with nobody coming.
  if (turnLooksSilent(log) && elapsedSec >= ABANDONED_AFTER_S) {
    // …unless the server says someone is driving it. Then the silence is a slow
    // turn on a pod this viewer is not subscribed to — the case that made every
    // time-based verdict here wrong, and the reason there is now a fact to ask
    // for instead of a clock to interpret.
    if (alive === true) {
      return (
        <div className={className} style={box} data-testid="turn-still-working">
          還在跑,只是這段時間沒有輸出。
        </div>
      );
    }
    return (
      <div className={className} style={box} data-testid="turn-abandoned">
        這一輪已經 {Math.floor(elapsedSec / 60)} 分鐘沒有任何動靜。
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

  // The model endpoint asked us to slow down and the turn is holding before it
  // retries. Waiting is the only cure for a 429, so this silence is deliberate
  // and can run for a minute — without a line saying so it reads as a hang.
  // Takes precedence over the metrics line for the same reason the restore
  // does: what the user needs to know is why nothing is happening.
  if (log.rateLimited != null) {
    return (
      <div className={className} style={box}>
        請求過於頻繁,{Math.ceil(log.rateLimited.seconds)} 秒後自動重試…
        {elapsedSec >= 1 && <span style={{ opacity: 0.7 }}> · {elapsedSec}s</span>}
      </div>
    );
  }

  // #739: BELOW the branches above it, deliberately — hoisting it over them
  // made the abandoned-turn detector (and its retry button) unreachable for the
  // whole compaction round trip, which is exactly when a turn looks stuck. The
  // idle guard is what needed relaxing, not this branch's position.
  if (compactingLive) {
    return (
      <div className={className} style={box} data-testid="turn-compacting">
        整理較早的對話…
        {compactSec >= 1 && <span style={{ opacity: 0.7 }}> · {compactSec}s</span>}
      </div>
    );
  }

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

  // #748: a reasoning model can sit in `thinking` for a long time. The backend
  // pushes metrics throughout it (reasoning deltas count toward the completion
  // chars), but the line was rendered only for `answering` — so the longest
  // silence of a turn was also its emptiest. Nothing was broken; the numbers
  // had nowhere to go.
  //
  // The counts go BESIDE 思考中 rather than replacing it: the words say what the
  // model is doing, the numbers say it is still doing it. Swapping one for the
  // other trades a known problem for its mirror image.
  // The model has produced output and is answering (or is mid tool-call): the
  // token line (↑/↓ tok · tok/s) is the whole signal.
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
      {/* #748: a reasoning model can sit in `thinking` for minutes, and the
          backend pushes counts throughout it — they simply had nowhere to go.
          They are appended HERE rather than in a branch of their own, so this
          line keeps the retry affordance and the FE-anchored clock; an earlier
          attempt returned early and lost both, printing the backend's elapsed,
          which is the very number this component exists to distrust. */}
      {phase === "thinking" && log.metrics && (
        // Counts only: the elapsed on this line is the FE-anchored one below.
        <span style={{ opacity: 0.7 }}> · {formatCounts(log.metrics)}</span>
      )}
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
