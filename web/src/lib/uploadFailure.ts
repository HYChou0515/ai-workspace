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
import { quotaKind } from "./quotaFailure";

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
  const kind = quotaKind(status, code);
  if (kind === "user") return "workspace.upload.userFull";
  // `environment` is not reachable from an upload — the write path never asks
  // the sandbox admission gate — but mapping it keeps this total rather than
  // relying on that staying true.
  if (kind === "environment") return "workspace.upload.envFull";
  if (kind === "workspace") return "workspace.upload.full";
  if (status === 413) return "workspace.upload.failed";
  // Anything else reports WHAT happened rather than guessing WHY: blaming the
  // size cap for a dropped connection sends the user after a limit that was
  // never involved.
  return "workspace.upload.error";
}
