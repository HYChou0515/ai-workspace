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

/** The clause each limit contributes when it is not the one that led. */
export const QUOTA_ALSO_KEY = {
  workspace: "resources.also.workspace",
  user: "resources.also.user",
  environment: "resources.also.environment",
} as const;

export function quotaKind(status: number | undefined, code: string | undefined): QuotaKind {
  if (status !== 507) return null;
  if (code === "user_quota_exceeded") return "user";
  if (code === "sandbox_quota_exceeded") return "environment";
  // No code, or the pre-existing one: an older backend, or a body that was not
  // JSON. The per-workspace meaning is the safe default — it is what 507 meant
  // before the other two limits existed.
  return "workspace";
}

/**
 * Every limit a refusal named, primary first.
 *
 * A turn is gated on more than one rule, and reporting only the first to fire
 * produced a sequence that reads as a bug even though each message is true: the
 * person frees disk, sends again, and is told about their environment limit
 * instead. The first message implied that acting on it would be enough.
 *
 * `also` is absent from every other 507 — a single refusal still yields exactly
 * one kind, so nothing else has to change.
 */
export function quotaKinds(
  status: number | undefined,
  code: string | undefined,
  also: string[] | undefined,
): QuotaKind[] {
  const primary = quotaKind(status, code);
  if (!primary) return [];
  return [primary, ...(also ?? []).map((c) => quotaKind(status, c)).filter((k) => k !== null)];
}
