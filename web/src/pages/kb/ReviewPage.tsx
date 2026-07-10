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
import { type MsgKey, useT } from "../../lib/i18n";
import { ClusterReviewList } from "./ClusterReviewList";
import { ReviewTable } from "./ReviewTable";
import { SuppressedAuditList } from "./SuppressedAuditList";

type View = "pending" | "resolved" | "grouped" | "suppressed";

const TABS: { view: View; label: MsgKey }[] = [
  { view: "pending", label: "review.tab.pending" },
  { view: "grouped", label: "review.tab.grouped" },
  { view: "resolved", label: "review.tab.resolved" },
  { view: "suppressed", label: "review.tab.suppressed" },
];

export function ReviewPage() {
  const t = useT();
  useBreadcrumbs([{ label: t("nav.home"), to: "/" }, { label: t("review.title") }]);
  const [view, setView] = useState<View>("pending");
  // #506 P7: the grouped view collapses duplicate/related items by concept; the
  // pending/resolved views keep the flat filterable table.
  const { query, ...actions } = useReviewInbox({
    resolved: view === "resolved",
    grouped: view === "grouped",
    suppressed: view === "suppressed",
  });
  const inbox = query.data;

  return (
    <div className="rvw-page">
      <header className="rvw-page__head">
        <h1 className="rvw-page__title">{t("review.title")}</h1>
        <p className="rvw-page__sub">{t("review.subtitle")}</p>
      </header>

      <div className="kb-tabs" role="tablist" aria-label={t("review.title")}>
        {TABS.map((tab) => (
          <button
            key={tab.view}
            type="button"
            role="tab"
            aria-selected={view === tab.view}
            className={`kb-tab${view === tab.view ? " is-active" : ""}`}
            onClick={() => setView(tab.view)}
          >
            {t(tab.label)}
          </button>
        ))}
      </div>

      {query.isPending || !inbox ? (
        <div data-testid="review-loading" className="rvw-page__body">
          <Skeleton style={{ height: 220 }} />
        </div>
      ) : (
        <div className="rvw-page__body">
          {view === "grouped" ? (
            <ClusterReviewList clusters={inbox.clusters ?? []} actions={{ query, ...actions }} />
          ) : view === "suppressed" ? (
            <SuppressedAuditList items={inbox.suppressed ?? []} />
          ) : (
            <ReviewTable
              cards={inbox.cards}
              questions={inbox.questions}
              resolved={view === "resolved"}
              actions={{ query, ...actions }}
            />
          )}
        </div>
      )}
    </div>
  );
}
