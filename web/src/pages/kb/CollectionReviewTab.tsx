/**
 * The collection's 待審核 tab (#415, redesigned #481) — the SAME filterable review
 * table as the global 審核 inbox, scoped to THIS collection. One surface over the
 * two pending-item kinds (card-gen proposals + clarification questions) with inline
 * accept/reject, a detail drawer, and bulk apply. Reusing `ReviewTable` keeps a
 * single design + behaviour across the global page and every collection's tab.
 */
import { kbApi, type KbApi } from "../../api/kb";
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

  return (
    <div className="kb-review">
      <header className="kb-review__head">
        <h2 className="kb-review__title">{t("kb.tab.review")}</h2>
        <p className="kb-review__sub">{t("kb.review.subtitle")}</p>
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
