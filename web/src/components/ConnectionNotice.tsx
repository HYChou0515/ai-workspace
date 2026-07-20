import type { ChatConnection } from "../hooks/useChatSession";
import { pxToRem } from "../lib/pxToRem";

/**
 * Tells the viewer their live stream dropped, so a frozen answer reads as a wait
 * rather than a crash.
 *
 * Losing the stream used to be entirely silent — the subscription's `catch`
 * swallowed every error, not even logging — while events published during the
 * gap are dropped (there is no replay), so the answer simply stopped growing.
 * The content is not at risk: the turn is persisted and re-read on reconnect.
 * What was missing was saying so.
 *
 * Deliberately quiet on the FIRST connect: a notice on first paint would flag
 * every page load as a problem, which is how a warning stops being read.
 */
export function ConnectionNotice({ connection }: { connection: ChatConnection }) {
  if (connection.state !== "reconnecting") return null;

  // One failed retry is a blip; a run of them is an outage. Saying the same
  // thing either way is how the old spinner became meaningless.
  const sustained = connection.attempts >= 3;

  return (
    <div data-testid="connection-notice" role="status" style={box(sustained)}>
      <span style={{ fontWeight: 600 }}>
        {sustained ? "連線持續中斷" : "連線中斷，重新連線中…"}
      </span>{" "}
      <span style={{ color: "var(--text-paper-d)" }}>
        {sustained
          ? "仍在重試。這段期間的回覆不會遺失，重新整理即可看到。"
          : "回覆仍在進行，不會遺失。"}
      </span>
    </div>
  );
}

const box = (sustained: boolean): React.CSSProperties => ({
  padding: "6px 10px",
  borderRadius: "var(--radius-btn)",
  fontSize: pxToRem(12),
  lineHeight: 1.5,
  background: "var(--paper-2)",
  // A sustained outage earns the warning colour on its border only — a filled
  // banner would compete with the answer it is explaining.
  border: `1px solid ${sustained ? "var(--warn)" : "var(--paper-3)"}`,
});
