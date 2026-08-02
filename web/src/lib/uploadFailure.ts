/**
 * Which message an upload failure deserves.
 *
 * A status alone does not decide it: THREE different limits answer 507 and each
 * needs a different remedy — delete in this workspace, delete in some other item
 * you own, or close a live environment (which has nothing to do with files at
 * all). The backend distinguishes them in the body precisely so the UI does not
 * have to guess; reporting them all as "this workspace is full" sends two thirds
 * of the people who hit them somewhere there is nothing to fix.
 *
 * Pure, so the choice is testable without driving an `alert`.
 */
import type { MsgKey } from "./i18n";

/** The five outcomes this can name — typed so a renamed message key is a
 *  compile error rather than a silently missing string. */
export type UploadFailureKey = Extract<
  MsgKey,
  | "workspace.upload.full"
  | "workspace.upload.userFull"
  | "workspace.upload.envFull"
  | "workspace.upload.failed"
  | "workspace.upload.error"
>;

export function uploadFailureKey(
  status: number | undefined,
  code: string | undefined,
): UploadFailureKey {
  if (status === 507) {
    if (code === "user_quota_exceeded") return "workspace.upload.userFull";
    if (code === "sandbox_quota_exceeded") return "workspace.upload.envFull";
    return "workspace.upload.full"; // this workspace — the #245/#538 case
  }
  if (status === 413) return "workspace.upload.failed";
  // Anything else reports WHAT happened rather than guessing WHY: blaming the
  // size cap for a dropped connection sends the user after a limit that was
  // never involved.
  return "workspace.upload.error";
}
