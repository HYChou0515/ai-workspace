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

export function SuppressedAuditList({ items }: { items: KbSuppressedItem[] }) {
  const t = useT();
  if (items.length === 0) {
    return (
      <p data-testid="suppressed-empty" className="rvw-cluster__empty">
        {t("review.suppressed.empty")}
      </p>
    );
  }
  return (
    <ul className="rvw-suppressed-list">
      {items.map((it, i) => (
        <li
          key={`${it.collection_id}:${it.cluster_key}:${it.kind}:${i}`}
          className="rvw-suppressed"
        >
          <span className="rvw-suppressed__label">{it.label || it.cluster_key}</span>
          <span className="rvw-suppressed__reason">
            {t(REASON_KEY[it.reason] ?? "review.suppressed.reason.other")}
          </span>
          <span className="rvw-cluster__coll">{it.collection_name}</span>
        </li>
      ))}
    </ul>
  );
}
