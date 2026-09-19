/**
 * The Sandbox modal's frame: it fetches, it owns the two size drafts and the
 * one Save that commits them, and it is the modal the panel is shown in.
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
 * SAVE, not blur. The fields used to commit the moment focus left them, and
 * there was no Save: Escape wrote nothing, Tab wrote, whether a ✕ click wrote
 * depended on the browser, and a "you have unsaved changes" prompt could not
 * be added because the prompt's own focus move was a blur and so a save. With
 * a Save button the modal is like every other one (#779): a draft is dirty
 * when it differs from what the modal opened with, every deliberate exit —
 * Escape, Cancel — goes through `useDirtyClose`, and nothing is written by a
 * keystroke that only moved focus. One PUT carries BOTH dimensions: the route
 * replaces both, and sending only the edited one used to clear the other.
 *
 * Because saves are dispatched while this is on screen, a refusal has
 * somewhere to be read (`saveFailed`) and the draft stays for a second try.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { itemEnvironmentApi } from "../api/itemEnvironment";
import { myResourcesApi } from "../api/myResources";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { ItemEnvironmentPanel, type SizeDraft } from "./ItemEnvironmentPanel";
import { toSizeString } from "./ItemEnvironmentSize";
import { ModalShell } from "./ModalShell";
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
  const budget = budgetFrom(resources.data);

  // What the modal OPENED with — the baseline `dirty` is measured against. A
  // draft of `null` means "nothing typed yet": the fields show the stated
  // values, and are clean by definition.
  const stated: SizeDraft | null = env.data
    ? {
        cpu: env.data.statedCpuCores === null ? "" : String(env.data.statedCpuCores),
        memory: toSizeString(env.data.statedMemoryBytes) ?? "",
      }
    : null;
  const [draft, setDraft] = useState<SizeDraft | null>(null);
  const current = draft ?? stated;
  const dirty =
    current !== null && stated !== null && (current.cpu !== stated.cpu || current.memory !== stated.memory);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["item-environment", slug, itemId] });
    void qc.invalidateQueries({ queryKey: ["my-resources"] });
  };

  const save = useMutation({
    mutationFn: (d: SizeDraft) =>
      itemEnvironmentApi.setSize(slug, itemId, {
        cpuCores: d.cpu === "" ? null : Number(d.cpu),
        memory: d.memory === "" ? null : d.memory,
      }),
    onSuccess: () => {
      // The stated values are about to become what was typed; drop the draft
      // so the fields follow the refetched record instead of a stale copy.
      setDraft(null);
      refresh();
    },
  });
  const close = useMutation({
    mutationFn: () => myResourcesApi.closeEnvironment(itemId),
    // Both queries: closing frees the person's budget as well as this item's
    // sandbox, so leaving the total stale would show a gauge that has not
    // noticed what the button just did.
    onSuccess: refresh,
  });

  const attemptClose = useDirtyClose(dirty, onClose);
  const editable = env.data !== undefined && budget !== null && canEdit;
  const canSave = editable && dirty && !env.data!.running && !save.isPending;

  return (
    <ModalShell
      onClose={attemptClose}
      labelledBy={titleId}
      data-testid="item-environment-modal"
      width={480}
      maxWidth="92vw"
      panelStyle={{ padding: 18, display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}
    >
      {/* A heading ELEMENT: `labelledBy` names the dialog either way, but a
          <strong> leaves the dialog with nothing for heading navigation to
          land on. Sized like the Tools modal's title beside it. */}
      <h2 id={titleId} style={{ margin: 0, fontSize: pxToRem(14), fontWeight: 600 }}>
        {t("itemenv.heading")}
      </h2>
      <p style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
        {t("itemenv.tip")}
      </p>

      {env.data && current ? (
        <ItemEnvironmentPanel
          env={env.data}
          budget={budget}
          canEdit={canEdit}
          draft={current}
          onDraft={setDraft}
          onCloseSandbox={() => {
            // Otherwise the "not saved" line from an earlier refusal is still
            // sitting there after the sandbox has been shut down and the panel
            // has re-rendered around it, describing a request nobody can see.
            save.reset();
            close.mutate();
          }}
        />
      ) : (
        <p
          data-testid="item-environment-pending"
          className="detail"
          style={{ margin: 0, color: "var(--text-paper-d)" }}
        >
          {t(env.isError ? "itemenv.loadFailed" : "itemenv.loading")}
        </p>
      )}

      {save.isError ? (
        <p data-testid="save-failed" className="detail" role="alert" style={{ margin: 0, color: "var(--err)" }}>
          {t("itemenv.saveFailed")}
        </p>
      ) : null}

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 2 }}>
        {editable ? (
          <>
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              data-testid="itemenv-cancel"
              onClick={attemptClose}
            >
              {t("tools.cancel")}
            </button>
            <button
              type="button"
              className="btn"
              data-variant="primary"
              data-size="sm"
              data-testid="itemenv-save"
              onClick={() => current && save.mutate(current)}
              disabled={!canSave}
            >
              {t("tools.save")}
            </button>
          </>
        ) : (
          // Nothing to save — no budget on this deploy, or a viewer who may
          // look but not spend — so the one way out is named as such.
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            data-testid="itemenv-close-panel"
            onClick={attemptClose}
          >
            {t("itemenv.dismiss")}
          </button>
        )}
      </div>
    </ModalShell>
  );
}
