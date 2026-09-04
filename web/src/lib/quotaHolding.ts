/**
 * What a "you are at your environment limit" refusal offers to do about itself.
 *
 * `SandboxQuotaExceeded` has always promised, in its own docstring, that "the
 * only useful thing to tell someone is what to close and how much it buys
 * back". The numbers answered the second half; this is the first.
 *
 * It matters more since items default to their App's ceiling: hitting the limit
 * became ordinary rather than exceptional, so this is not an error path, it is
 * the feature's normal moment. Being told you are full and left to find the
 * right page turns that moment into a dead end.
 */

/** One live environment holding the person's quota. */
export type QuotaHolder = {
  itemId: string;
  /** Empty when the backend could not resolve it (a deleted item). Renders as
   *  the id rather than as a blank row — addressable beats invisible. */
  title: string;
  cpuCores: number;
  memoryBytes: number;
};

type HoldingWire = {
  item_id?: unknown;
  title?: unknown;
  cpu_cores?: unknown;
  memory_bytes?: unknown;
};

type QuotaDetailish = { error?: unknown; holding?: unknown } & Record<string, unknown>;

/**
 * The closable environments named in a refusal body, or an empty list.
 *
 * Empty is a normal answer with three different causes, and none of them is an
 * error: the refusal was not about environments (disk has files to delete, not
 * environments to close), the viewer is not the owner and the backend withheld
 * the list, or the body predates this field. The caller renders "no list" for
 * all three — never a spinner, never a failure.
 */
export function quotaHolding(detail: QuotaDetailish | undefined): QuotaHolder[] {
  if (!detail || detail.error !== "sandbox_quota_exceeded") return [];
  const rows = Array.isArray(detail.holding) ? (detail.holding as HoldingWire[]) : [];
  return rows.flatMap((r) => {
    const itemId = typeof r.item_id === "string" ? r.item_id : "";
    // A row with no id cannot be closed, and a button that cannot act is worse
    // than a missing one — it reads as a promise.
    if (!itemId) return [];
    return [
      {
        itemId,
        title: typeof r.title === "string" ? r.title : "",
        cpuCores: typeof r.cpu_cores === "number" ? r.cpu_cores : 0,
        memoryBytes: typeof r.memory_bytes === "number" ? r.memory_bytes : 0,
      },
    ];
  });
}
