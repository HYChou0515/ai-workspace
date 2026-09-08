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
 * The dirty guard is not optional decoration. The size fields commit on BLUR,
 * and the old `×` happened to save on the way out because clicking it moved
 * focus. Escape does not, so the very act of making this a real modal is what
 * introduces a way to lose a typed number — `useDirtyClose` goes in with it.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { itemEnvironmentApi } from "../api/itemEnvironment";
import { myResourcesApi } from "../api/myResources";
import { useDirtyClose } from "../hooks/useDirtyClose";
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
  // Reported UP by the panel, because that is where the drafts are made.
  const [dirty, setDirty] = useState(false);

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

  // Every deliberate exit — Escape, and the ✕ this component draws itself —
  // goes through this one handler. A ✕ still wired to the bare `onClose` would
  // throw the work away in silence while Escape politely asked.
  const attemptClose = useDirtyClose(dirty, onClose);

  if (!env.data) return null;

  return (
    <ModalShell
      onClose={attemptClose}
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
          // Lines up with `.item-environment`'s own padding below it, so the
          // title and the first reading share a left edge.
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
          onClick={attemptClose}
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
        onDirtyChange={setDirty}
      />
    </ModalShell>
  );
}
