/**
 * SuppressedAuditList (#506 P7) — the audit of what the reconcile step AUTO-DROPPED.
 *
 * When a candidate is already explained, reconcile suppresses it instead of adding
 * it to the review queue (⑥). What counts as "already explained" depends on the kind:
 * a CARD proposal only ever loses to a near-duplicate existing card, while a TERM
 * QUESTION also loses to the wiki (don't ask what is already written down). Those are
 * never silently lost: each is kept as an auditable member and listed here with WHY
 * it was dropped, so a human can verify nothing was wrongly discarded.
 */
import type { KbSuppressedItem } from "../../api/kb";
import { type MsgKey, useT } from "../../lib/i18n";

/** The human reason for a suppression — never show the raw `wiki` / `near-card` slug. */
const REASON_KEY: Record<string, MsgKey> = {
  wiki: "review.suppressed.reason.wiki",
  "near-card": "review.suppressed.reason.near-card",
};

/** #506/#577 follow-up — the KIND of a suppressed candidate, so a reader can tell a
 * suppressed CARD from a suppressed QUESTION. Crucial because a card is NEVER
 * wiki-suppressed (#577): a "reason: wiki" row is always a question, and reading it
 * as "wiki is killing my cards" is exactly the confusion this label prevents. */
const KIND_KEY: Record<string, MsgKey> = {
  proposal: "review.type.card",
  term_question: "review.type.question",
  card: "review.type.card",
};

export function SuppressedAuditList({ items }: { items: KbSuppressedItem[] }) {
  const t = useT();
  if (items.length === 0) {
    return (
      <p data-testid="suppressed-empty" className="rvw-cluster__empty">
        {t("review.suppressed.empty")}
      </p>
    );
  }
  const cards = items.filter((it) => it.kind === "proposal" || it.kind === "card").length;
  const questions = items.length - cards;
  return (
    <div>
      <p data-testid="suppressed-summary" className="rvw-suppressed__summary">
        {t("review.suppressed.summary")
          .replace("{cards}", String(cards))
          .replace("{questions}", String(questions))}
      </p>
      <ul className="rvw-suppressed-list">
        {items.map((it, i) => (
          <li
            key={`${it.collection_id}:${it.cluster_key}:${it.kind}:${i}`}
            className="rvw-suppressed"
          >
            <span
              data-testid={`suppressed-kind-${it.kind}`}
              className="rvw-suppressed__kind"
            >
              {t(KIND_KEY[it.kind] ?? "review.type.card")}
            </span>
            <span className="rvw-suppressed__label">{it.label || it.cluster_key}</span>
            <span className="rvw-suppressed__reason">
              {it.reason === "near-card" && it.target_label
                ? t("review.suppressed.reason.near-card.named", { card: it.target_label })
                : t(REASON_KEY[it.reason] ?? "review.suppressed.reason.other")}
            </span>
            <span className="rvw-cluster__coll">{it.collection_name}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
