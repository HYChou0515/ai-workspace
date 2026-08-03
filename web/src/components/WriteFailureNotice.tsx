/**
 * Says so when a write did not save.
 *
 * Mounted once, above every page, and fed by the QueryClient's mutation cache
 * (`api/queryClient.ts`) rather than by any call site. That is the point: the
 * bug it closes was not one panel forgetting to check an error, it was 135
 * mutations sharing a default of silence — the env-var panel took a Participant's
 * API keys, PATCHed them, got a 403, and closed as if it had saved.
 *
 * It reports, it does not diagnose. The headline says what happened to the
 * user's work; the server's own message rides underneath in smaller type for
 * whoever needs it. Anything that can explain a failure better in place should
 * keep doing so and opt out with `meta: { silentError: true }`.
 */
import { useSyncExternalStore } from "react";

import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import {
  currentWriteFailure,
  dismissWriteFailure,
  subscribeWriteFailures,
} from "../lib/writeFailures";

export function WriteFailureNotice() {
  const t = useT();
  const failure = useSyncExternalStore(
    subscribeWriteFailures,
    currentWriteFailure,
    currentWriteFailure,
  );
  if (!failure) return null;

  // 403 is the one status worth naming, because its remedy is not "try again" —
  // it is "ask someone". Every other failure gets the neutral line; guessing at
  // a cause we did not read would send people to look in the wrong place.
  const headline = failure.status === 403 ? t("writeFailure.forbidden") : t("writeFailure.generic");

  return (
    <div
      data-testid="write-failure"
      role="alert"
      style={{
        position: "fixed",
        // Bottom-centre: out of the way of the top bar's own controls, and not
        // over the composer, which is where the user's hands are.
        bottom: 18,
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 60,
        maxWidth: "min(560px, calc(100vw - 32px))",
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
        padding: "10px 12px",
        borderRadius: "var(--radius-btn)",
        background: "var(--paper-2)",
        border: "1px solid var(--err, var(--warn))",
        boxShadow: "0 6px 20px rgba(0,0,0,0.18)",
        fontSize: pxToRem(13),
        lineHeight: 1.5,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 600 }}>{headline}</div>
        <div
          style={{
            color: "var(--text-paper-d)",
            fontSize: pxToRem(11),
            marginTop: 2,
            // A long server message must not stretch the banner off-screen.
            overflowWrap: "anywhere",
          }}
        >
          {failure.message}
        </div>
      </div>
      <button
        type="button"
        className="btn"
        data-variant="secondary"
        data-size="sm"
        data-testid="write-failure-dismiss"
        onClick={dismissWriteFailure}
      >
        {t("writeFailure.dismiss")}
      </button>
    </div>
  );
}
