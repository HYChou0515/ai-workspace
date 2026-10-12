import type { ClipboardEvent, DragEvent } from "react";

/** `onCopy` / `onCut` / `onDragStart` for a secret masked by `data-masked`
 * (src/styles/autofill.test.ts). A password field refuses all three; a text
 * field drawn as dots would hand the plain value to the clipboard, or to
 * whatever field it is dragged into. Read from the element, so a Show toggle
 * that flips `data-masked` lets the shown value go with no second handler. */
export function blockMaskedCopy(e: ClipboardEvent<HTMLInputElement> | DragEvent<HTMLInputElement>): void {
  if (e.currentTarget.dataset.masked === "true") e.preventDefault();
}
