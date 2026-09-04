import { useCallback } from "react";

import { useDialog } from "../components/Dialog";
import { useT } from "../lib/i18n";

/**
 * The one way a modal says "I have something the user would lose".
 *
 * Wrap the modal's `onClose` and hand the result to `ModalShell` — every
 * deliberate exit (Escape, ✕, Cancel) then goes through the same prompt,
 * because they all funnel through `onClose`. Accidental exits are a separate
 * concern: `ModalShell` withdraws the backdrop by default, and a stray click
 * beside the panel does nothing rather than raising a dialog about an action
 * the user never meant to take.
 */
export function useDirtyClose(dirty: boolean, onClose: () => void): () => void {
  const dialog = useDialog();
  const t = useT();
  return useCallback(() => {
    if (!dirty) {
      onClose();
      return;
    }
    void (async () => {
      const choice = await dialog.confirm({
        title: t("dirtyClose.prompt"),
        actions: [
          { id: "keep", label: t("dirtyClose.keep") },
          { id: "discard", label: t("dirtyClose.discard"), variant: "danger" },
        ],
      });
      if (choice === "discard") onClose();
    })();
  }, [dirty, onClose, dialog, t]);
}
