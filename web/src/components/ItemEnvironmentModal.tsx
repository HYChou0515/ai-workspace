/**
 * The environment panel's frame: it fetches, and it decides what the two halves
 * are allowed to say.
 *
 * TWO queries, and they are deliberately different routes. `/environment` is
 * scoped to this item and is what a collaborator may see; `/me/resources` is
 * scoped to a PERSON and carries their whole working set. Asking the second for
 * this item's figure would mean handing a visitor the owner's other items to
 * explain one number — which is why the first exists at all.
 *
 * A failing `/me/resources` is not fatal here: without it there is simply no
 * budget half, which is the same state a deploy that caps nobody is in. The
 * status half — is it running, close it — stands on its own.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { itemEnvironmentApi } from "../api/itemEnvironment";
import { myResourcesApi } from "../api/myResources";
import { ItemEnvironmentPanel } from "./ItemEnvironmentPanel";
import { budgetFrom } from "./useItemEnvironment";

export type ItemEnvironmentModalProps = {
  slug: string;
  itemId: string;
  /** Whether this viewer holds `change_permission` — the verb that decides who
   *  may spend the OWNER's quota. Everyone else sees the same numbers, greyed. */
  canEdit: boolean;
  onClose: () => void;
};

export function ItemEnvironmentModal({
  slug,
  itemId,
  canEdit,
  onClose,
}: ItemEnvironmentModalProps) {
  const qc = useQueryClient();

  const env = useQuery({
    queryKey: ["item-environment", slug, itemId],
    queryFn: () => itemEnvironmentApi.get(slug, itemId),
  });
  const resources = useQuery({
    queryKey: ["my-resources"],
    queryFn: () => myResourcesApi.get(),
    // A person without a budget is the normal case, not an error state.
    retry: false,
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["item-environment", slug, itemId] });
    void qc.invalidateQueries({ queryKey: ["my-resources"] });
  };

  const save = useMutation({
    mutationFn: (cpu: number | null) =>
      itemEnvironmentApi.setSize(slug, itemId, { cpuCores: cpu, memory: null }),
    onSuccess: refresh,
  });
  const close = useMutation({
    mutationFn: () => myResourcesApi.closeEnvironment(itemId),
    // Both queries: closing frees the person's budget as well as this item's
    // environment, so leaving the total stale would show a gauge that has not
    // noticed what the button just did.
    onSuccess: refresh,
  });

  if (!env.data) return null;

  return (
    <div className="modal" role="dialog" aria-modal="true">
      <ItemEnvironmentPanel
        env={env.data}
        budget={budgetFrom(resources.data)}
        canEdit={canEdit}
        slug={slug}
        itemId={itemId}
        onClose={() => close.mutate()}
        onSave={(cpu) => save.mutate(cpu)}
      />
      <button type="button" onClick={onClose}>
        ×
      </button>
    </div>
  );
}
