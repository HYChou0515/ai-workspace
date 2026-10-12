/**
 * A failure, said so a reader can act on it (docs/plan-friendly-errors.md D1):
 * what happened, what they can do, and — collapsed, with a copy button — the
 * raw text for whoever debugs it. Everyone can open it: a person reporting a
 * problem pastes it, and "it said there was an error" is all a report carries
 * otherwise.
 *
 * The one component for every surface that shows a failure, so a turn's error
 * box, its persisted banner and a job's status line read alike.
 */
import { type ReactNode, useState } from "react";

import { describeFailure, type Failure } from "../lib/failure";
import { useLocale, useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { ResourceLinkText } from "./ResourceLinkText";

export function ErrorNotice({
  failure,
  live = false,
  children,
}: {
  failure: Failure;
  /** A failure happening NOW (the turn's own error box) is announced; one
   *  re-read from the thread is not. */
  live?: boolean;
  /** What else the surface offers beside it (the environments a quota refusal
   *  is holding). */
  children?: ReactNode;
}) {
  const [locale] = useLocale();
  const { headline, action, details } = describeFailure(failure, locale);

  return (
    <div
      data-testid="error-notice"
      role={live ? "alert" : undefined}
      style={{
        padding: "8px 10px",
        border: "1px solid var(--err)",
        borderRadius: "var(--radius-card)",
        fontSize: pxToRem(13),
        lineHeight: 1.5,
        // A long raw message must not stretch the chat column.
        overflowWrap: "anywhere",
      }}
    >
      {/* #692: a refusal that names /my-resources links there. */}
      <div style={{ fontWeight: 600, color: "var(--err)" }}>
        <ResourceLinkText text={headline} />
      </div>
      {action && <div style={{ color: "var(--text-paper-d)" }}>{action}</div>}
      {children}
      {details && <FailureDetails raw={details} />}
    </div>
  );
}

/** The raw text of a failure, collapsed under "technical details" with a copy
 *  button — the one shape every surface that shows a failure uses for it, so a
 *  person reporting a problem finds it in the same place everywhere. */
export function FailureDetails({ raw }: { raw: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  return (
    <details data-testid="error-notice-details" style={{ marginTop: 4 }}>
      <summary style={{ cursor: "pointer", fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
        {t("failure.details")}
      </summary>
      <pre
        style={{
          margin: "4px 0",
          whiteSpace: "pre-wrap",
          fontFamily: "var(--font-mono)",
          fontSize: pxToRem(12),
        }}
      >
        {raw}
      </pre>
      <button
        type="button"
        className="btn"
        onClick={() => {
          void navigator.clipboard?.writeText(raw).then(() => setCopied(true));
        }}
      >
        {copied ? t("failure.copied") : t("failure.copy")}
      </button>
    </details>
  );
}
