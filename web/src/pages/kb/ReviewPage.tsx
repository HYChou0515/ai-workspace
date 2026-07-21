/**
 * ReviewPage (#481) — the global 審核 inbox: every pending-review item (card-gen
 * proposals + clarification questions) across every collection the user may read.
 * Four tabs — pending / by-concept / handled / auto-skipped — over the SAME
 * server-paginated + server-filtered stream (#506 G2): this page owns the toolbar
 * (search / collection / type / actionable / group-by-run) and the prev/next Pager,
 * so the backend caps each fetch to one page and the FE never loads thousands of
 * rows. The view components below are pure presenters of one page.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { kbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { Skeleton } from "../../components/Skeleton";
import { useBreadcrumbs } from "../../hooks/breadcrumbs";
import { useReviewInbox } from "../../hooks/useReviewInbox";
import { type MsgKey, useT } from "../../lib/i18n";
import { ClusterReviewList } from "./ClusterReviewList";
import { EntityMergeList } from "./EntityMergeList";
import { Pager } from "./Pager";
import { ReviewTable } from "./ReviewTable";
import { SuppressedAuditList } from "./SuppressedAuditList";

type View = "pending" | "resolved" | "grouped" | "suppressed" | "merges";
type TypeFilter = "all" | "card" | "question";

const TABS: { view: View; label: MsgKey }[] = [
  { view: "pending", label: "review.tab.pending" },
  { view: "grouped", label: "review.tab.grouped" },
  { view: "resolved", label: "review.tab.resolved" },
  { view: "suppressed", label: "review.tab.suppressed" },
  // #534 B: name pairs an AI thinks are one thing. Same act as every other tab —
  // one pending item, two answers — so it lives here rather than on a page of
  // its own.
  { view: "merges", label: "review.tab.merges" },
];

const PAGE_SIZE = 50;
const KIND: Record<TypeFilter, "all" | "cards" | "questions"> = {
  all: "all",
  card: "cards",
  question: "questions",
};

export function ReviewPage() {
  const t = useT();
  const queryClient = useQueryClient();
  useBreadcrumbs([{ label: t("nav.home"), to: "/" }, { label: t("review.title") }]);
  const [view, setView] = useState<View>("pending");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState(""); // the debounced search actually sent to the server
  const [collectionId, setCollectionId] = useState("");
  const [type, setType] = useState<TypeFilter>("all");
  const [actionable, setActionable] = useState(false);
  const [groupByRun, setGroupByRun] = useState(false);
  const [offset, setOffset] = useState(0);

  // Debounce the search box so a keystroke doesn't fire a query per character.
  useEffect(() => {
    const id = setTimeout(() => setQ(search), 250);
    return () => clearTimeout(id);
  }, [search]);
  // Any tab or filter change returns to the first page (offset only advances via
  // the Pager, which is NOT in these deps).
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset on filter change
  useEffect(() => setOffset(0), [view, q, collectionId, type, actionable]);

  const isFlat = view === "pending" || view === "resolved";
  const isMerges = view === "merges";
  const collections = useQuery({
    queryKey: qk.kb.collections,
    queryFn: () => kbApi.listCollections(),
  });

  // The collection filter goes to the SERVER: it is a boundary of responsibility,
  // not a view preference — different collections are reviewed by different
  // people. The kind filter stays here, because it is how one reviewer narrows
  // what they are looking at within their own queue.
  const [kind, setKind] = useState("");
  const merges = useQuery({
    queryKey: qk.kb.graphProposals(collectionId || undefined),
    queryFn: () => kbApi.listGraphProposals(collectionId || undefined),
    enabled: view === "merges",
  });
  const mergeKinds = Array.from(
    new Set((merges.data ?? []).flatMap((p) => [p.kind, p.other_kind]).filter(Boolean)),
  ).sort();
  const shownMerges = (merges.data ?? []).filter(
    (p) => !kind || p.kind === kind || p.other_kind === kind,
  );
  const decideMerge = useMutation({
    mutationFn: ({ a, b, same }: { a: string; b: string; same: boolean }) =>
      kbApi.decideGraphProposal(a, b, same),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["kb", "graph", "proposals"] }),
  });

  const { query, ...actions } = useReviewInbox({
    resolved: view === "resolved",
    grouped: view === "grouped",
    suppressed: view === "suppressed",
    collectionId: collectionId || undefined,
    kind: isFlat ? KIND[type] : "all",
    q: q || undefined,
    actionable: view === "suppressed" ? false : actionable,
    limit: PAGE_SIZE,
    offset,
  });
  const inbox = query.data;
  const total = inbox?.total ?? 0;

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

      <div className="rvw__toolbar" role="search">
        {!isMerges && (
          <label className="rvw__search">
            <Icon name="search" size={14} color="var(--text-paper-d2)" />
            <input
              type="search"
              aria-label={t("review.filter.search")}
              placeholder={t("review.filter.search")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
        )}
        <select
          className="inline-edit"
          aria-label={t("review.filter.collection")}
          value={collectionId}
          onChange={(e) => setCollectionId(e.target.value)}
        >
          <option value="">{t("review.filter.collection")}</option>
          {(collections.data ?? []).map((c) => (
            <option key={c.resource_id} value={c.resource_id}>
              {c.name}
            </option>
          ))}
        </select>
        {isMerges && (
          <select
            className="inline-edit"
            aria-label={t("merge.filterKind")}
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            <option value="">{t("merge.filterKind")}</option>
            {mergeKinds.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        )}
        {isFlat && (
          <select
            className="inline-edit"
            aria-label={t("review.filter.type")}
            value={type}
            onChange={(e) => setType(e.target.value as TypeFilter)}
          >
            <option value="all">{t("review.filter.type")}</option>
            <option value="card">{t("review.type.card")}</option>
            <option value="question">{t("review.type.question")}</option>
          </select>
        )}
        {view !== "suppressed" && !isMerges && (
          <label className="rvw__check">
            <input
              type="checkbox"
              checked={actionable}
              onChange={(e) => setActionable(e.target.checked)}
            />
            {t("review.filter.actionable")}
          </label>
        )}
        {isFlat && (
          <label className="rvw__check">
            <input
              type="checkbox"
              checked={groupByRun}
              onChange={(e) => setGroupByRun(e.target.checked)}
            />
            {t("review.groupByRun")}
          </label>
        )}
      </div>

      {isMerges ? (
        <div className="rvw-page__body">
          {merges.isPending ? (
            <Skeleton style={{ height: 220 }} />
          ) : (
            <EntityMergeList
              proposals={shownMerges}
              onAccept={(a, b) => decideMerge.mutate({ a, b, same: true })}
              onReject={(a, b) => decideMerge.mutate({ a, b, same: false })}
            />
          )}
        </div>
      ) : query.isPending || !inbox ? (
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
              groupByRun={groupByRun}
              actions={{ query, ...actions }}
            />
          )}
          <Pager total={total} offset={offset} pageSize={PAGE_SIZE} onOffset={setOffset} />
        </div>
      )}
    </div>
  );
}
