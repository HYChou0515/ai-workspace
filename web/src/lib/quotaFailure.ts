/**
 * Which resource limit answered a 507, if any.
 *
 * Three rules share that status and need three different remedies:
 *
 * | code                     | what is full                | what to do            |
 * |--------------------------|-----------------------------|-----------------------|
 * | `workspace_quota_exceeded` | this item's workspace     | delete here           |
 * | `user_quota_exceeded`      | your total, across items  | delete somewhere else |
 * | `sandbox_quota_exceeded`   | live environments (no files involved) | close one |
 *
 * Which of them a caller can even see depends on WHICH request it made — an
 * upload never reaches the sandbox admission gate, and a terminal command never
 * writes a file — so each surface maps this to its own wording. What must not
 * happen is any surface guessing from the status alone: the review found the
 * upload path doing exactly that, telling people their workspace was full when
 * the space to free was in a different item, or when nothing was full but their
 * environments.
 */
export type QuotaKind = "workspace" | "user" | "environment" | null;

export function quotaKind(status: number | undefined, code: string | undefined): QuotaKind {
  if (status !== 507) return null;
  if (code === "user_quota_exceeded") return "user";
  if (code === "sandbox_quota_exceeded") return "environment";
  // No code, or the pre-existing one: an older backend, or a body that was not
  // JSON. The per-workspace meaning is the safe default — it is what 507 meant
  // before the other two limits existed.
  return "workspace";
}
