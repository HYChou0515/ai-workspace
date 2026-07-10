/**
 * #481: data hooks for the global 審核 review inbox.
 *
 * `useReviewInbox` reads one view (pending, or the resolved history; optionally
 * scoped to a collection) and exposes the mutations that act on it — inline
 * accept/reject, a drawer edit, the multi-card commit, and answer/discard for
 * questions. Every mutation invalidates the whole `["kb","review-inbox"]` prefix
 * (so pending + history + every collection scope refresh together) plus the
 * downstream reads a commit/answer writes into (context cards, per-collection
 * queues). `useReviewBadgeCount` is the nav badge — how many actionable items
 * (the ones the caller may write) are waiting.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { kbApi, type KbApi, type KbCardRef, type KbProposedCard } from "../api/kb";
import { qk } from "../api/queryKeys";

const REVIEW_PREFIX = ["kb", "review-inbox"] as const;

export type ReviewInboxOpts = {
  resolved?: boolean;
  collectionId?: string;
  grouped?: boolean;
  suppressed?: boolean;
};

export function useReviewInbox(opts: ReviewInboxOpts = {}, client: KbApi = kbApi) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: qk.kb.reviewInbox(opts),
    queryFn: () => client.getReviewInbox(opts),
  });

  const invalidate = () => {
    // A decide/commit/answer changes what's pending vs resolved and can write
    // context cards / clarification pages, so refresh the inbox and the reads a
    // write lands in.
    qc.invalidateQueries({ queryKey: REVIEW_PREFIX });
    qc.invalidateQueries({ queryKey: ["kb", "context-cards"] });
    qc.invalidateQueries({ queryKey: ["kb", "doc-questions"] });
    qc.invalidateQueries({ queryKey: ["kb", "card-gen-runs"] });
  };

  const decide = useMutation({
    mutationFn: (v: { runId: string; cardId: string; decision: string }) =>
      client.decideCard(v.runId, v.cardId, v.decision),
    onSuccess: invalidate,
  });
  const update = useMutation({
    mutationFn: (v: { runId: string; card: KbProposedCard }) =>
      client.updateProposal(v.runId, v.card),
    onSuccess: invalidate,
  });
  const commit = useMutation({
    mutationFn: (cards: KbCardRef[]) => client.commitCards(cards),
    onSuccess: invalidate,
  });
  const answer = useMutation({
    mutationFn: (v: { id: string; answer: string }) => client.answerDocQuestion(v.id, v.answer),
    onSuccess: invalidate,
  });
  const discard = useMutation({
    mutationFn: (id: string) => client.discardDocQuestion(id),
    onSuccess: invalidate,
  });

  return { query, decide, update, commit, answer, discard };
}

/** The nav badge: how many pending items the caller can actually act on (#481 —
 * "only count what I can operate on"). 0 ⇒ the badge is hidden. */
export function useReviewBadgeCount(client: KbApi = kbApi): number {
  const { data } = useQuery({
    queryKey: qk.kb.reviewInbox({}),
    queryFn: () => client.getReviewInbox({}),
  });
  if (!data) return 0;
  return (
    data.cards.filter((c) => c.can_act).length + data.questions.filter((q) => q.can_act).length
  );
}
