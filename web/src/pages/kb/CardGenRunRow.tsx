/**
 * One card-gen run row in the 待審核 inbox (#415). Collapsed it's a summary
 * ("N 張卡片提案") with a 略過 (dismiss) action; expanded it lazy-loads the run's
 * proposals and drives CardGenReview — the same review UI the modal used — with
 * save / commit wired to this run. Committing or dismissing resolves the run
 * server-side, so `onResolved` refetches the queue and the row drops out.
 */
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { KbApi, KbCardGenCommit, KbCardGenRun, KbProposedCard } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { CardGenReview } from "./CardGenReview";

export function CardGenRunRow({
  run,
  client,
  onResolved,
}: {
  run: KbCardGenRun;
  client: KbApi;
  onResolved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [proposals, setProposals] = useState<KbProposedCard[] | null>(null);
  const [committed, setCommitted] = useState<KbCardGenCommit | null>(null);

  const status = useQuery({
    queryKey: qk.kb.cardGen(run.run_id),
    queryFn: () => client.getCardGenStatus(run.run_id),
    enabled: open,
  });
  const { data: existingCards = [] } = useQuery({
    queryKey: qk.kb.contextCards(run.collection_id),
    queryFn: () => client.listContextCards(run.collection_id),
    enabled: open,
  });
  // Seed the editable proposals once, from the run's persisted set.
  useEffect(() => {
    if (open && status.data && proposals === null) setProposals(status.data.proposals);
  }, [open, status.data, proposals]);

  const saveMut = useMutation({
    mutationFn: () => client.reviewCardGen(run.run_id, proposals ?? []),
  });
  const commitMut = useMutation({
    mutationFn: async () => {
      await client.reviewCardGen(run.run_id, proposals ?? []); // persist before committing
      return client.commitCardGen(run.run_id);
    },
    onSuccess: (r) => {
      setCommitted(r);
      onResolved();
    },
  });
  const dismissMut = useMutation({
    mutationFn: () => client.dismissCardGen(run.run_id),
    onSuccess: onResolved,
  });

  return (
    <li className="kb-review__item" data-testid="review-run">
      <div className="kb-review__row">
        <button
          type="button"
          className="kb-review__toggle"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          {run.proposal_count} 張卡片提案
        </button>
        <button
          type="button"
          className="kb-review__dismiss"
          onClick={() => dismissMut.mutate()}
          disabled={dismissMut.isPending}
        >
          略過
        </button>
      </div>
      {open && proposals !== null && (
        <div className="kb-review__body">
          <CardGenReview
            proposals={proposals}
            existingCards={existingCards}
            onChange={setProposals}
            onSave={() => saveMut.mutate()}
            onCommit={() => commitMut.mutate()}
            committed={committed}
            saving={saveMut.isPending}
            committing={commitMut.isPending}
          />
        </div>
      )}
    </li>
  );
}
