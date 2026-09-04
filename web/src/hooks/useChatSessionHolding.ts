/**
 * The one place a send refusal still has its structure.
 *
 * `useChatSession` reduces a failed send to a STRING for the composer notice,
 * which is right for a sentence and lossy for a list — after that line the
 * closable environments are gone. Adversarial review found the consequence:
 * the backend emitted `holding`, `quotaHolding` parsed it, `QuotaHoldingList`
 * rendered it, and nothing joined them, so §1.8's "the wall must be a door"
 * lived entirely in unit tests.
 *
 * Kept as a separate function rather than inlined so the extraction is testable
 * without a DOM, a transport, or a chat session — the join is the part that was
 * missing, so the join is the part that gets pinned.
 */

import { quotaHolding, type QuotaHolder } from "../lib/quotaHolding";

/**
 * The environments a send refusal says are holding the budget, or an empty
 * list for every other outcome.
 *
 * Empty is an ordinary answer with several innocent causes — a non-quota
 * failure, a disk refusal (which has files to delete, not environments to
 * close), or a collaborator the backend withheld the inventory from — and the
 * caller renders all of them as absence.
 */
export function holdingFromSendError(err: unknown): QuotaHolder[] {
  if (!err || typeof err !== "object") return [];
  const detail = (err as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return [];
  return quotaHolding(detail as Record<string, unknown>);
}
