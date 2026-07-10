/**
 * ClusterReviewList (#506 P7) — the review inbox grouped BY CONCEPT.
 *
 * The reconcile step (P6) tags each proposal + question with a cluster_key, so a
 * card and a question about the same thing — or duplicate proposals from several
 * runs — share one cluster. This renders one collapsible row per cluster (⑤): the
 * reviewer sees the concept once, with a member count, and expands it to read the
 * grouped proposals + questions instead of scrolling past N near-identical rows.
 *
 * With `actions` (the review-inbox mutations) each expanded member gets an inline
 * accept/reject (cards) or an answer drawer (questions), so a whole duplicate set
 * can be triaged in place without leaving the grouped view. Read-only members (a
 * collection the user can see but not write) show no action.
 */
import { useState } from "react";

import type { KbReviewCard, KbReviewCluster } from "../../api/kb";
import { Btn } from "../../components/Btn";
import { Icon } from "../../components/Icon";
import type { useReviewInbox } from "../../hooks/useReviewInbox";
import { useT } from "../../lib/i18n";
import { ReviewDrawer, type ReviewItem } from "./ReviewDrawer";

type Actions = ReturnType<typeof useReviewInbox>;

/** The concept's display label — the first proposal's title/key, else the first
 * question's term, else the raw cluster key. */
function conceptLabel(c: KbReviewCluster): string {
  return (
    c.cards[0]?.card.title ||
    c.cards[0]?.card.keys[0] ||
    c.questions[0]?.question.term ||
    c.cluster_key
  );
}

export function ClusterReviewList({
  clusters,
  actions,
  resolved = false,
}: {
  clusters: KbReviewCluster[];
  actions?: Actions;
  resolved?: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState<ReviewItem | null>(null);
  if (clusters.length === 0) {
    return (
      <p data-testid="cluster-empty" className="rvw-cluster__empty">
        {t("review.cluster.empty")}
      </p>
    );
  }
  return (
    <>
      <ul className="rvw-cluster-list">
        {clusters.map((c) => (
          <ClusterRow
            key={`${c.collection_id}:${c.cluster_key}`}
            cluster={c}
            actions={actions}
            resolved={resolved}
            onOpen={setOpen}
          />
        ))}
      </ul>
      {open && actions && (
        <ReviewDrawer
          item={open}
          resolved={resolved}
          actions={actions}
          onClose={() => setOpen(null)}
        />
      )}
    </>
  );
}

function ClusterRow({
  cluster,
  actions,
  resolved,
  onOpen,
}: {
  cluster: KbReviewCluster;
  actions?: Actions;
  resolved: boolean;
  onOpen: (item: ReviewItem) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const label = conceptLabel(cluster);
  const count = t("review.cluster.count", { n: cluster.size });
  // Toggle: clicking the current decision again clears it back to pending (same
  // semantics as the flat table's inline accept/reject).
  const decide = (c: KbReviewCard, decision: "accepted" | "rejected") =>
    actions?.decide.mutate({
      runId: c.run_id,
      cardId: c.card.id,
      decision: c.card.decision === decision ? "pending" : decision,
    });
  return (
    <li className="rvw-cluster">
      <button
        type="button"
        className="rvw-cluster__head"
        aria-expanded={open}
        aria-label={`${label} · ${count}`}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name={open ? "arrow_d" : "arrow_r"} size={12} />
        <span className="rvw-cluster__label">{label}</span>
        <span className="rvw-cluster__count">{cluster.size}</span>
        <span className="rvw-cluster__coll">{cluster.collection_name}</span>
      </button>
      {open && (
        <div className="rvw-cluster__members">
          {cluster.cards.map((c) => (
            <div key={`card:${c.run_id}:${c.card.id}`} className="rvw-cluster__member">
              <span className="rvw-cluster__kind rvw-cluster__kind--card">card</span>
              <span className="rvw-cluster__memberlabel">{c.card.title || c.card.keys[0]}</span>
              {actions && !resolved && c.can_act && (
                <span className="rvw-cluster__acts">
                  <Btn
                    size="sm"
                    variant={c.card.decision === "accepted" ? "primary" : "ghost"}
                    active={c.card.decision === "accepted"}
                    onClick={() => decide(c, "accepted")}
                  >
                    {t("review.action.accept")}
                  </Btn>
                  <Btn
                    size="sm"
                    variant={c.card.decision === "rejected" ? "danger" : "ghost"}
                    active={c.card.decision === "rejected"}
                    onClick={() => decide(c, "rejected")}
                  >
                    {t("review.action.reject")}
                  </Btn>
                </span>
              )}
            </div>
          ))}
          {cluster.questions.map((q) => (
            <div key={`q:${q.question.id}`} className="rvw-cluster__member">
              <span className="rvw-cluster__kind rvw-cluster__kind--q">question</span>
              <span className="rvw-cluster__memberlabel">
                {q.question.term || q.question.question_text}
              </span>
              {actions && !resolved && q.can_act && (
                <span className="rvw-cluster__acts">
                  <Btn
                    size="sm"
                    variant="ghost"
                    onClick={() => onOpen({ kind: "question", data: q })}
                  >
                    {t("review.action.answer")}
                  </Btn>
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </li>
  );
}
