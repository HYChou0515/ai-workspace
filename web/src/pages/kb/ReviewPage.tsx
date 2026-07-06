/**
 * ReviewPage (#481) — the global 審核 inbox: every pending-review item (card-gen
 * proposals + clarification questions) across every collection the user may read,
 * in one filterable table. A pending / handled toggle switches between the live
 * queue and the resolved history. Absorbs the old (invisible) /clarifications page.
 */
import { useState } from "react";

import { Skeleton } from "../../components/Skeleton";
import { useBreadcrumbs } from "../../hooks/breadcrumbs";
import { useReviewInbox } from "../../hooks/useReviewInbox";
import { useT } from "../../lib/i18n";
import { ReviewTable } from "./ReviewTable";

export function ReviewPage() {
  const t = useT();
  useBreadcrumbs([{ label: t("nav.home"), to: "/" }, { label: t("review.title") }]);
  const [resolved, setResolved] = useState(false);
  const { query, ...actions } = useReviewInbox({ resolved });
  const inbox = query.data;

  return (
    <div className="rvw-page">
      <header className="rvw-page__head">
        <h1 className="rvw-page__title">{t("review.title")}</h1>
        <p className="rvw-page__sub">{t("review.subtitle")}</p>
      </header>

      <div className="kb-tabs" role="tablist" aria-label={t("review.title")}>
        <button
          type="button"
          role="tab"
          aria-selected={!resolved}
          className={`kb-tab${!resolved ? " is-active" : ""}`}
          onClick={() => setResolved(false)}
        >
          {t("review.tab.pending")}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={resolved}
          className={`kb-tab${resolved ? " is-active" : ""}`}
          onClick={() => setResolved(true)}
        >
          {t("review.tab.resolved")}
        </button>
      </div>

      {query.isPending || !inbox ? (
        <div data-testid="review-loading" className="rvw-page__body">
          <Skeleton style={{ height: 220 }} />
        </div>
      ) : (
        <div className="rvw-page__body">
          <ReviewTable
            cards={inbox.cards}
            questions={inbox.questions}
            resolved={resolved}
            actions={{ query, ...actions }}
          />
        </div>
      )}
    </div>
  );
}
