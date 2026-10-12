/**
 * A failure as the reader is shown it (docs/plan-friendly-errors.md D1/D5):
 * what happened, what they can do, and — collapsed, for whoever debugs it —
 * the raw text the backend kept.
 *
 * The backend names the failure (`failover.classify.ERROR_KINDS`) and keeps
 * the raw text as the message; the WORDS are chosen here, in the reader's
 * language. A kind with no copy — a newer backend, or a row persisted before
 * kinds existed (`error`) — reads as `unknown`: never the raw string as the
 * whole message, which is what this exists to stop.
 */

import { type Locale, type MsgKey, translate } from "./i18n";

export type Failure = {
  /** The backend's kind, or a front-end one (`quota`, `send_failed`, …). */
  kind: string;
  /** The numbers the kind's copy says (`waited_s`, `attempts`). */
  params?: Record<string, number> | null;
  /** The technical details: the raw text, as the backend or the request kept it. */
  raw: string;
  /** For a writer that already has the whole sentence (a quota refusal). */
  headline?: string;
  /** What to do, when the surface's remedy is not the kind's own — a document
   *  is re-read, not sent again. */
  action?: string;
};

export type FailureText = {
  headline: string;
  /** What the reader can do about it; null when the writer's own headline says it. */
  action: string | null;
  /** The raw text, or null when it says nothing the headline did not. */
  details: string | null;
};

/** Kinds with their own copy. Anything else is `unknown`. */
const WORDED = new Set([
  "rate_limited",
  "all_busy",
  "auth",
  "timeout",
  "connection",
  "provider_error",
  "context_overflow",
  "bad_request",
  "unknown",
  // Our own API (P6, `failureFromHttp`).
  "forbidden",
  "server_error",
]);

/** The variant of a kind's headline that says the number, when it was counted. */
const COUNTED: Record<string, { param: string; key: MsgKey }> = {
  rate_limited: { param: "waited_s", key: "failure.rate_limited.waited" },
  timeout: { param: "attempts", key: "failure.timeout.tried" },
};

/** A failure caught in the browser (a request that was refused, a stream that
 *  dropped): the thrown message is its technical details. */
export function failureFromError(err: unknown, kind = "unknown", headline?: string): Failure {
  const raw = err instanceof Error ? err.message : String(err);
  return headline === undefined ? { kind, raw } : { kind, raw, headline };
}

/** A request OUR API refused (P6). Only the two answers with a remedy of their
 *  own are named — "ask for access" and "the server broke, not you"; any other
 *  status is `unknown` with its status line kept as the details, rather than
 *  copy guessed for a status nobody has seen in this app. */
export function failureFromHttp(err: unknown): Failure {
  const status = (err as { status?: number } | null)?.status;
  if (status === 403) return failureFromError(err, "forbidden");
  if (status !== undefined && status >= 500) return failureFromError(err, "server_error");
  return failureFromError(err);
}

/** A document that failed to process (P5): worded from the kind the backend
 *  named its exception by; with no kind (a row from before kinds, or a failure
 *  that was a sentence to begin with — "processing was interrupted") the page's
 *  own `fallback` is the headline. The detail is the raw text either way. */
export function docFailure(
  doc: { status_detail?: string | null; status_error_kind?: string | null },
  locale: Locale,
): Failure {
  const raw = doc.status_detail ?? "";
  // The kind's own remedy is a chat's ("send it again", "split the message");
  // a document's is to re-read it.
  const action = translate(locale, "kb.doc.failedAction");
  return doc.status_error_kind
    ? { kind: doc.status_error_kind, raw, action }
    : { kind: "doc_failed", headline: translate(locale, "kb.doc.processingFailed"), raw, action };
}

/** A workflow run that ended in error (P5), from its `result` — null when it
 *  recorded none. A step's own failure leads with its REASON, which was written
 *  for the reader; anything else is worded from its kind. */
export function runFailure(result: Record<string, unknown> | null | undefined): Failure | null {
  const raw = result?.error;
  if (typeof raw !== "string") return null;
  const kind = typeof result?.error_kind === "string" ? result.error_kind : "unknown";
  const reason = result?.error_reason;
  if (kind === "step_failed" && typeof reason === "string" && reason) {
    return { kind, headline: reason, raw };
  }
  return { kind, raw };
}

/** One line for a developer's log: the headline, then the raw text. */
export function failureLine(failure: Failure, locale: Locale): string {
  const { headline, details } = describeFailure(failure, locale);
  return details ? `${headline} — ${details}` : headline;
}

export function describeFailure(failure: Failure, locale: Locale): FailureText {
  const raw = failure.raw.trim();
  if (failure.headline) {
    return {
      headline: failure.headline,
      action: null,
      details: raw && raw !== failure.headline ? raw : null,
    };
  }
  const kind = WORDED.has(failure.kind) ? failure.kind : "unknown";
  const counted = COUNTED[kind];
  const n = counted ? failure.params?.[counted.param] : undefined;
  const headline =
    counted && n != null
      ? translate(locale, counted.key, { [counted.param]: n })
      : translate(locale, `failure.${kind}.what` as MsgKey);
  return {
    headline,
    action: failure.action ?? translate(locale, `failure.${kind}.do` as MsgKey),
    details: raw || null,
  };
}
