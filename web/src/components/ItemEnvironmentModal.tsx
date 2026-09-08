/**
 * The sandbox panel's frame: it fetches, it decides what the two halves are
 * allowed to say, and it is the modal the panel is shown in.
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
 *
 * It was called a modal long before it was one. What it rendered was a bare
 * `<div className="modal">`, and `.modal` is declared in no stylesheet — so it
 * was an unstyled block in the normal document flow: nothing dimmed behind it,
 * no Escape, focus left on the page underneath, and one unlabelled `×` sitting
 * below the panel. `ModalShell` owns all of that (#445/#779), so this asks for
 * it rather than re-deriving it.
 *
 * Closing is only closing. The fields commit on BLUR and there is no Save
 * button, so what "leaving" ought to mean is a question about the panel's save
 * model rather than about this modal, and two attempts to answer it here were
 * both worse than leaving it alone.
 *
 * A `useDirtyClose` prompt cannot work at all: `DialogProvider` focuses the
 * confirm in order for it to be answerable, taking focus blurs the field, and
 * blurring is what saves — so the question "discard this?" committed the value
 * it was asking about, by design and in every browser. Committing on the way
 * out instead made Escape the only keystroke in the app that WRITES, spending
 * the item OWNER's quota, and the 507 that answers it could not be shown
 * because this component is unmounted by then.
 *
 * So Escape closes, as it does in every other modal here, and a number typed
 * but never blurred is not sent. That is the same outcome as typing one and
 * navigating away today, and it errs in the safe direction: a dropped keystroke
 * rather than a write nobody confirmed. Making that number reachable — a Save
 * button, or a visible refusal that outlives the panel — is a change to the
 * save model, not to the frame around it.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId } from "react";

import { itemEnvironmentApi } from "../api/itemEnvironment";
import { myResourcesApi } from "../api/myResources";
import { useT } from "../lib/i18n";
import { Icon } from "./Icon";
import { ItemEnvironmentPanel } from "./ItemEnvironmentPanel";
import { ModalShell } from "./ModalShell";
import { budgetFrom } from "./useItemEnvironment";
import { type SizeEdit, sizeToSave } from "./ItemEnvironmentSize";

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
  const t = useT();
  const qc = useQueryClient();
  const titleId = useId();

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
    // The route REPLACES both dimensions, so the client owns the whole value.
    // Hard-coding `memory: null` here meant every cpu edit — and every "back to
    // default" click — silently destroyed a stored memory setting.
    mutationFn: (edit: SizeEdit) =>
      itemEnvironmentApi.setSize(slug, itemId, sizeToSave(env.data!, edit)),
    onSuccess: refresh,
  });
  const close = useMutation({
    mutationFn: () => myResourcesApi.closeEnvironment(itemId),
    // Both queries: closing frees the person's budget as well as this item's
    // sandbox, so leaving the total stale would show a gauge that has not
    // noticed what the button just did.
    onSuccess: refresh,
  });

  if (!env.data) return null;

  return (
    <ModalShell
      onClose={onClose}
      labelledBy={titleId}
      data-testid="item-environment-modal"
      width={420}
      maxWidth="92vw"
      panelStyle={{ display: "flex", flexDirection: "column", minHeight: 0 }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-8)",
          // Same padding as `.item-environment` below it, so the header BOX
          // shares the panel's margins. The title text itself sits one icon
          // further in, exactly as it does in every other modal's header —
          // measured at 1280: title 466px, first reading 443px.
          padding: "var(--space-12) var(--space-12) 0",
        }}
      >
        <Icon name="settings" size={15} />
        <strong id={titleId} style={{ flex: 1, minWidth: 0 }}>
          {t("itemenv.heading")}
        </strong>
        <button
          type="button"
          data-testid="dismiss-item-environment"
          // NOT `itemenv.close` — that button ends what is running. This one
          // only puts the panel away.
          aria-label={t("itemenv.dismiss")}
          onClick={onClose}
          style={{ border: "none", background: "transparent", cursor: "pointer" }}
        >
          <Icon name="x" size={14} />
        </button>
      </div>

      <ItemEnvironmentPanel
        env={env.data}
        budget={budgetFrom(resources.data)}
        canEdit={canEdit}
        onClose={() => close.mutate()}
        onSave={(edit) => save.mutate(edit)}
      />
    </ModalShell>
  );
}
