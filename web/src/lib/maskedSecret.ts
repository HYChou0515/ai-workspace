import type { ClipboardEvent } from "react";

/** `onCopy` / `onCut` for a secret masked by `data-masked` (src/styles/autofill.test.ts).
 * A password field refuses both; a text field drawn as dots would hand the plain
 * value to the clipboard. Read from the element, so a Show toggle that flips
 * `data-masked` lets the shown value be copied with no second handler. */
export function blockMaskedCopy(e: ClipboardEvent<HTMLInputElement>): void {
  if (e.currentTarget.dataset.masked === "true") e.preventDefault();
}
