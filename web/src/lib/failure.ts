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
    action: translate(locale, `failure.${kind}.do` as MsgKey),
    details: raw || null,
  };
}
