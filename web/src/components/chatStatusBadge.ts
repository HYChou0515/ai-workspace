/**
 * The status badge for a workflow chat row (#132) — symbol + label + tone, so the
 * list distinguishes a workflow chat at a glance without opening it. A free chat
 * (null status) or an unrecognised value gets no badge.
 */
export type ChatBadge = { symbol: string; label: string; tone: "active" | "wait" | "ok" | "bad" };

export function chatStatusBadge(status: string | null): ChatBadge | null {
  switch (status) {
    case "running":
    case "pending":
      return { symbol: "●", label: "running", tone: "active" };
    case "awaiting_human":
      return { symbol: "⏸", label: "awaiting", tone: "wait" };
    case "done":
      return { symbol: "✓", label: "done", tone: "ok" };
    case "error":
      return { symbol: "!", label: "error", tone: "bad" };
    case "cancelled":
      return { symbol: "×", label: "cancelled", tone: "bad" };
    default:
      return null;
  }
}
