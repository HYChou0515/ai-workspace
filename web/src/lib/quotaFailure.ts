import { formatBytes } from "./bytes";
import type { MsgKey, Vars } from "./i18n";

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

/** The `detail` object a 507 carries. Shapes differ per limit — see `quotaAmounts`. */
export type QuotaDetail = {
  error?: string;
  dimension?: string;
  used?: number;
  limit?: number;
  quota?: number;
  also?: string[];
};

/**
 * How much of the limit is held, and what the limit is — as display strings.
 *
 * The messages named a limit but never a number, which made them impossible to
 * check: "this item's workspace is full" reads identically whether it is true
 * or whether the wrong rule answered. That ambiguity was reported as a bug, and
 * from the screen alone it could not be told apart from a real full workspace.
 *
 * Each dimension is read in its OWN unit. The environment limit is three
 * different things (a count of sandboxes, cores, bytes) and rendering cores as
 * "1 B" would be worse than saying nothing. A body with no numbers returns
 * null — nothing is invented to fill the sentence.
 */
export const QUOTA_DIMENSION_KEY = {
  sandboxes: "resources.gauge.count",
  cpu: "resources.gauge.cpu",
  memory: "resources.memory",
} as const;

export function quotaAmounts(
  detail: QuotaDetail | undefined,
): { used: string; limit: string; dimension?: keyof typeof QUOTA_DIMENSION_KEY } | null {
  if (!detail) return null;
  const isEnv = detail.error === "sandbox_quota_exceeded";
  const cap = isEnv ? detail.limit : detail.quota;
  if (typeof detail.used !== "number" || typeof cap !== "number") return null;
  const asBytes = !isEnv || detail.dimension === "memory";
  const render = asBytes ? formatBytes : (n: number) => `${n}`;
  // The environment limit is THREE limits behind one sentence, and the sentence
  // names none of them. Appending a bare "2 of 1" to "you are at your limit for
  // live environments" told a person holding ONE environment that they had 4 —
  // those were cores. A number that contradicts the sentence it is attached to
  // is worse than no number, which is the whole reason numbers were added.
  const dimension =
    isEnv && detail.dimension && detail.dimension in QUOTA_DIMENSION_KEY
      ? (detail.dimension as keyof typeof QUOTA_DIMENSION_KEY)
      : undefined;
  return { used: render(detail.used), limit: render(cap), ...(dimension ? { dimension } : {}) };
}

/**
 * The clause each limit contributes when it is not the one that led.
 *
 * There is no `environment` entry: the backend appends the admission refusal
 * FIRST, so whenever the environment limit binds it is the one that leads and
 * can never appear in `also`. A key for it would be a message nothing can
 * produce, sitting in the catalogue looking maintained.
 */
export const QUOTA_ALSO_KEY = {
  workspace: "resources.also.workspace",
  user: "resources.also.user",
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


/**
 * The whole refusal sentence: the surface's own wording for the limit that led,
 * the numbers behind it, and one clause per other limit that also bound.
 *
 * Shared rather than written per surface. The refusal wording has drifted
 * between entry points before — four copies of the same 507 body, only one of
 * which anyone maintained — and the chat and the terminal say the same thing
 * about the same limits.
 */
export function quotaMessage(
  t: (key: MsgKey, vars?: Vars) => string,
  leadKey: Record<NonNullable<QuotaKind>, MsgKey>,
  err: { status?: number; code?: string; also?: string[]; detail?: QuotaDetail } | null,
): string | null {
  // `code` is the field every caller has always carried; `detail` is the whole
  // body and only some paths pass it. Reading `detail` ALONE silently demoted
  // every caller that did not to the code-less default — the right sentence has
  // to survive a caller that only knows the code.
  const code = err?.code ?? err?.detail?.error;
  const [lead, ...rest] = quotaKinds(err?.status, code, err?.also ?? err?.detail?.also);
  if (!lead) return null;
  const amounts = quotaAmounts(err?.detail);
  const numbers = !amounts
    ? ""
    : amounts.dimension
      ? ` ${t("resources.usedOfLimitNamed", {
          what: t(QUOTA_DIMENSION_KEY[amounts.dimension]),
          used: amounts.used,
          limit: amounts.limit,
        })}`
      : ` ${t("resources.usedOfLimit", { used: amounts.used, limit: amounts.limit })}`;
  const head = t(leadKey[lead]) + numbers;
  const also = rest.filter((k): k is keyof typeof QUOTA_ALSO_KEY => k !== null && k in QUOTA_ALSO_KEY);
  return [head, ...also.map((k) => t(QUOTA_ALSO_KEY[k]))].join(" ");
}