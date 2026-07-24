/**
 * The collection's 待審核 tab (#415, redesigned #481) — the SAME filterable review
 * table as the global 審核 inbox, scoped to THIS collection. One surface over the
 * two pending-item kinds (card-gen proposals + clarification questions) with inline
 * accept/reject, a detail drawer, and bulk apply. Reusing `ReviewTable` keeps a
 * single design + behaviour across the global page and every collection's tab.
 */
import { useQuery } from "@tanstack/react-query";

import { kbApi, type KbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Skeleton } from "../../components/Skeleton";
import { useReviewInbox } from "../../hooks/useReviewInbox";
import { useT } from "../../lib/i18n";
import { ReviewTable } from "./ReviewTable";

export function CollectionReviewTab({
  collectionId,
  client = kbApi,
}: {
  collectionId: string;
  client?: KbApi;
}) {
  const t = useT();
  const { query, ...actions } = useReviewInbox({ collectionId }, client);
  const inbox = query.data;

  // #506/#577 follow-up: the last run's funnel (drafted → kept), so a thin queue is
  // explained ("drafter drafted little" vs "reconcile suppressed the rest") and P2's
  // effect is visible. null before any run — then no summary is shown.
  const { data: funnel } = useQuery({
    queryKey: qk.kb.cardGenLatest(collectionId),
    queryFn: () => client.getLatestCardGenFunnel(collectionId),
  });

  return (
    <div className="kb-review">
      <header className="kb-review__head">
        <h2 className="kb-review__title">{t("kb.tab.review")}</h2>
        <p className="kb-review__sub">{t("kb.review.subtitle")}</p>
        {funnel && (
          <p className="kb-review__funnel" data-testid="cardgen-funnel-summary">
            {t("kb.cards.funnel.summary", {
              units: funnel.n_units,
              drafts: funnel.n_raw_drafts,
              kept: funnel.n_proposals,
            })}
          </p>
        )}
      </header>
      {query.isPending || !inbox ? (
        <div data-testid="review-loading">
          <Skeleton style={{ height: 120 }} />
        </div>
      ) : (
        <ReviewTable
          cards={inbox.cards}
          questions={inbox.questions}
          resolved={false}
          actions={{ query, ...actions }}
          scoped
        />
      )}
    </div>
  );
}
