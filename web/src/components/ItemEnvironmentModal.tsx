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
 * The fields commit on BLUR and there is no Save button, so what "leaving"
 * ought to mean is a question about the panel's save model rather than about
 * this modal.
 *
 * The rule is about FOCUS, not about closing: moving focus off a field commits
 * it. Tab to the ✕, or click it in a browser that focuses buttons on mousedown,
 * and the field you left is saved — you moved the focus, and that is this
 * panel's only save gesture. Escape moves no focus and so sends nothing.
 *
 * That is inherited, and it is uneven: whether a ✕ CLICK saves depends on the
 * browser (Chrome focuses on mousedown, Firefox and Safari do not). Both were
 * tried here and both were worse than saying so. A `useDirtyClose` prompt
 * cannot work at all — `DialogProvider` focuses the confirm so it can be
 * answered, focus leaving the field blurs it, and blurring is what saves, so
 * the question commits the value it asks about. Committing on the way out made
 * Escape the only keystroke in the app that WRITES, spending the item owner's
 * quota. And withdrawing the ✕'s focus move with `preventDefault` — the third
 * attempt — evened the exits out by turning the commonest one into silent data
 * loss, which is a regression against what the ✕ does today.
 *
 * So the frame leaves the save model alone and this comment states it rather
 * than claiming it away. What would actually fix it is a Save button, which is
 * a decision about the panel, not about the modal around it.
 *
 * Because saves are dispatched while this is on screen, a refusal has somewhere
 * to be read (`saveFailed`) — the one part of that gap this frame can close.
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
        {/* A heading ELEMENT: `labelledBy` names the dialog either way, but a
            <strong> leaves the dialog with nothing for heading navigation to
            land on. */}
        <h2
          id={titleId}
          style={{
            flex: 1,
            minWidth: 0,
            margin: 0,
            fontSize: "var(--text-body)",
            fontWeight: 600,
          }}
        >
          {t("itemenv.heading")}
        </h2>
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

      {env.data ? (
        <ItemEnvironmentPanel
          env={env.data}
          budget={budgetFrom(resources.data)}
          canEdit={canEdit}
          onClose={() => {
            // Otherwise the "not saved" line from an earlier refusal is still
            // sitting there after the sandbox has been shut down and the panel
            // has re-rendered around it, describing a request nobody can see.
            save.reset();
            close.mutate();
          }}
          onSave={(edit) => save.mutate(edit)}
          saveFailed={save.isError}
        />
      ) : (
        <p
          data-testid="item-environment-pending"
          className="detail"
          style={{ margin: 0, padding: "var(--space-12)", color: "var(--text-paper-d)" }}
        >
          {t(env.isError ? "itemenv.loadFailed" : "itemenv.loading")}
        </p>
      )}
    </ModalShell>
  );
}
