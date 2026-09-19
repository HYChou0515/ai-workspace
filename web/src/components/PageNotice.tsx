import { useState } from "react";

import { useT } from "../lib/i18n";

/**
 * A page-level notice for the outcome of an action the person just took —
 * the thing a snackbar or GOV.UK's "notification banner (success)" does: one
 * sentence, at the top of the page they landed on, dismissable, announced to
 * assistive tech as a status (not an alert: nothing went wrong).
 *
 * Errors are NOT this component. A failure stays where the action was, next
 * to what failed (`role="alert"`, the pages' own failure lines); a notice is
 * for the success that took the person somewhere else — a transfer or a
 * delete leaving the entry page for the list (plan-skill-hub-ui-polish D10).
 */
export type PageNoticeContent = { kind: "success" | "info"; text: string };

export function PageNotice({
  notice,
}: {
  notice: PageNoticeContent | null | undefined;
}) {
  const t = useT();
  const [dismissed, setDismissed] = useState<PageNoticeContent | null>(null);
  if (!notice || dismissed === notice) return null;
  return (
    <div
      className="page-notice"
      data-kind={notice.kind}
      role="status"
      data-testid="page-notice"
    >
      <span className="page-notice-text">{notice.text}</span>
      <button
        type="button"
        className="btn"
        data-size="sm"
        data-variant="secondary"
        aria-label={t("notice.dismiss")}
        onClick={() => setDismissed(notice)}
      >
        {t("notice.dismiss")}
      </button>
    </div>
  );
}
