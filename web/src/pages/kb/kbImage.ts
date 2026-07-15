import type { KbImageInput } from "../../api/kb";

/** #513 P10: a transient image staged in the KB composer — the base64 payload sent
 * to the server plus display-only metadata for the preview chip. The image is never
 * uploaded as a KB document; it rides one message, the server describes it, done. */
export type StagedImage = KbImageInput & { name: string };

/** Read a File's bytes into the base64 payload a KB chat message carries. Uses
 * `arrayBuffer` (deterministic + testable) rather than FileReader's data URL. */
export async function fileToImageInput(file: File): Promise<StagedImage> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return { data: btoa(bin), mime: file.type, name: file.name };
}

/** A data URL for the composer's preview thumbnail (no object-URL lifecycle). */
export function stagedImagePreview(img: StagedImage): string {
  return `data:${img.mime};base64,${img.data}`;
}
