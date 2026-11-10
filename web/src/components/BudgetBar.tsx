/**
 * The asking person's LLM budget, in the row above a chat composer
 * (docs/plan-llm-budget.md P3/P4) — 「AI 剩 $11／共 $20」, remaining FIRST and
 * both labelled, so nobody has to guess which side of a slash is which.
 *
 * Shown, never enforced (D5): nothing here touches the send. What it shows is
 * whatever the deploy's class answered (`GET /me/llm-budget`):
 * - `off`, or `ok` with nothing to show → nothing at all;
 * - `unavailable` → a muted 「預算暫時查不到」 (D7), so a cost system that is down
 *   does not read as "there is no budget";
 * - the figure, then the deploy's message and link, as ONE cell of the status
 *   row beside the other gauges — it moves to a new line only when the row has
 *   no room (the user: 「擺不下才換行」; the row's CSS keeps a wrapped line from
 *   starting with a 「·」). The message is plain text, never HTML; the link is
 *   underlined.
 *
 * NN/g *Visibility of system status*: the money a turn spends is visible where
 * the turn is started. A `warn` / `critical` tone is said in a word before the
 * figure as well as in colour — the text stays ink, since `--warn` on paper is
 * too light to read at this size.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { ApiClient } from "../api/types";
import { useT } from "../lib/i18n";

/** An amount in its currency, cents only when there are any: $11, $3.50.
 *
 * One set of symbols for every reader — en-US's, where the dollar is `$` (the
 * user's example) and every other currency is told apart (`NT$11`, `€11`). In
 * the reader's own locale a TWD deploy read `$11` in zh-TW, the same as USD
 * (review round 1). An ISO-shaped code Intl does not know is said as given
 * (`XYZ 11`); one Intl refuses outright (`credits`, empty) falls back to the
 * rounded number and the code. */
export function formatMoney(amount: number, currency: string): string {
  const cents = Math.round(amount * 100) % 100 !== 0;
  const digits = { minimumFractionDigits: cents ? 2 : 0, maximumFractionDigits: 2 };
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency, ...digits }).format(amount);
  } catch {
    const n = new Intl.NumberFormat("en-US", digits).format(amount);
    return currency.trim() ? `${n} ${currency.trim()}` : n;
  }
}

export function BudgetBar({ client = api }: { client?: Pick<ApiClient, "getLlmBudget"> }) {
  const t = useT();
  const { data } = useQuery({ queryKey: qk.llmBudget, queryFn: () => client.getLlmBudget() });
  if (!data || data.status === "off") return null;
  if (data.status === "unavailable") {
    return (
      <span data-testid="llm-budget" style={{ color: "var(--text-paper-d)" }}>
        {t("budget.unavailable")}
      </span>
    );
  }
  const b = data.budget;
  if (!b) return null;

  const money = (n: number) => formatMoney(n, b.currency);
  const figure =
    b.remaining !== null && b.total !== null
      ? t("budget.remainingOfTotal", { remaining: money(b.remaining), total: money(b.total) })
      : b.remaining !== null
        ? t("budget.remaining", { remaining: money(b.remaining) })
        : b.total !== null
          ? t("budget.total", { total: money(b.total) })
          : null;
  if (!figure && !b.message && !b.link) return null;
  const toneWord = b.tone === "warn" ? t("budget.tone.warn") : b.tone === "critical" ? t("budget.tone.critical") : null;
  const mark = b.tone === "critical" ? "var(--err)" : b.tone === "warn" ? "var(--warn)" : null;

  // One cell: figure, message and link wrap together, and only inside the
  // cell when the cell alone is wider than the row.
  return (
    <span
      data-testid="llm-budget"
      data-tone={b.tone}
      style={{ display: "inline-flex", flexWrap: "wrap", alignItems: "center", columnGap: 6, rowGap: 2 }}
    >
      {mark ? <span aria-hidden style={{ width: 6, height: 6, borderRadius: "50%", background: mark, flex: "none" }} /> : null}
      {toneWord ? <strong style={{ color: "var(--text-paper)" }}>{toneWord}</strong> : null}
      {figure ? <span style={b.tone === "info" ? undefined : { color: "var(--text-paper)" }}>{figure}</span> : null}
      {b.message ? <span>{b.message}</span> : null}
      {b.link ? (
        <a
          href={b.link.url}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "var(--text-paper)", textDecoration: "underline", textUnderlineOffset: 2 }}
        >
          {b.link.label}
        </a>
      ) : null}
    </span>
  );
}
