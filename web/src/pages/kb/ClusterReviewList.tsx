/**
 * ClusterReviewList (#506 P7) — the review inbox grouped BY CONCEPT.
 *
 * The reconcile step (P6) tags each proposal + question with a cluster_key, so a
 * card and a question about the same thing — or duplicate proposals from several
 * runs — share one cluster. This renders one collapsible row per cluster (⑤): the
 * reviewer sees the concept once, with a member count, and expands it to read the
 * grouped proposals + questions instead of scrolling past N near-identical rows.
 */
import { useState } from "react";

import type { KbReviewCluster } from "../../api/kb";
import { Icon } from "../../components/Icon";
import { useT } from "../../lib/i18n";

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

export function ClusterReviewList({ clusters }: { clusters: KbReviewCluster[] }) {
  const t = useT();
  if (clusters.length === 0) {
    return (
      <p data-testid="cluster-empty" className="rvw-cluster__empty">
        {t("review.cluster.empty")}
      </p>
    );
  }
  return (
    <ul className="rvw-cluster-list">
      {clusters.map((c) => (
        <ClusterRow key={`${c.collection_id}:${c.cluster_key}`} cluster={c} />
      ))}
    </ul>
  );
}

function ClusterRow({ cluster }: { cluster: KbReviewCluster }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const label = conceptLabel(cluster);
  const count = t("review.cluster.count", { n: cluster.size });
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
              {c.card.title || c.card.keys[0]}
            </div>
          ))}
          {cluster.questions.map((q) => (
            <div key={`q:${q.question.id}`} className="rvw-cluster__member">
              <span className="rvw-cluster__kind rvw-cluster__kind--q">question</span>
              {q.question.term || q.question.question_text}
            </div>
          ))}
        </div>
      )}
    </li>
  );
}
