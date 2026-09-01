/**
 * How full this chat's context window is (#739 P2).
 *
 * The figure is anchored on the count the provider itself reported for the last
 * turn it measured — so it includes the system prompt, the tool schemas and the
 * skills index, none of which our estimator can see. Before this, the only
 * token number on screen was the length of the user's own message, which does
 * not move as the window fills; the first sign a conversation was full was it
 * being cut.
 *
 * When no ceiling is known the usage stands alone, with no bar and no
 * denominator: a gauge drawn against an invented ceiling is a number nobody
 * measured that everybody believes.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { ChatContextUsage } from "../api/types";
import { formatTokens } from "../lib/tokens";
import { pxToRem } from "../lib/pxToRem";

export function ContextBar({
  slug,
  itemId,
  chatId,
  load,
}: {
  slug: string;
  itemId: string;
  chatId: string;
  /** Injected in tests; defaults to the real endpoint. */
  load?: () => Promise<ChatContextUsage>;
}) {
  const { data } = useQuery({
    queryKey: qk.chatContext(slug, itemId, chatId),
    queryFn: () => (load ? load() : api.getChatContext(slug, itemId, chatId)),
  });
  if (!data) return null;

  const pct =
    data.limit && data.limit > 0
      ? Math.min(100, Math.round((data.used / data.limit) * 100))
      : null;
  return (
    <div
      data-testid="chat-context"
      style={{ display: "flex", flexDirection: "column", gap: 2 }}
    >
      {pct !== null && (
        <div
          style={{
            height: 3,
            background: "var(--paper-3)",
            borderRadius: 2,
            overflow: "hidden",
          }}
        >
          <div
            data-testid="chat-context-fill"
            style={{
              width: `${pct}%`,
              height: "100%",
              background: pct >= 90 ? "var(--warn)" : "var(--accent)",
            }}
          />
        </div>
      )}
      <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
        {pct === null
          ? formatTokens(data.used)
          : `${formatTokens(data.used)} / ${formatTokens(data.limit as number)}`}
      </span>
    </div>
  );
}
