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
 * - the deploy's message and link on a line of their own (`data-status-line`,
 *   like the storage-full warning), the message as plain text, never HTML.
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
import { useLocale, useT } from "../lib/i18n";

/** An amount in its currency, cents only when there are any: $11, $3.50. */
export function formatMoney(amount: number, currency: string, locale: string): string {
  const cents = Math.round(amount * 100) % 100 !== 0;
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      currencyDisplay: "narrowSymbol",
      minimumFractionDigits: cents ? 2 : 0,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    // Not an ISO currency code — say the number and the code as given.
    return `${amount} ${currency}`;
  }
}

export function BudgetBar({ client = api }: { client?: Pick<ApiClient, "getLlmBudget"> }) {
  const t = useT();
  const [locale] = useLocale();
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

  const money = (n: number) => formatMoney(n, b.currency, locale);
  const figure =
    b.remaining !== null && b.total !== null
      ? t("budget.remainingOfTotal", { remaining: money(b.remaining), total: money(b.total) })
      : b.remaining !== null
        ? t("budget.remaining", { remaining: money(b.remaining) })
        : b.total !== null
          ? t("budget.total", { total: money(b.total) })
          : null;
  const toneWord = b.tone === "warn" ? t("budget.tone.warn") : b.tone === "critical" ? t("budget.tone.critical") : null;
  const mark = b.tone === "critical" ? "var(--err)" : b.tone === "warn" ? "var(--warn)" : null;

  return (
    <>
      {figure ? (
        <span data-testid="llm-budget" data-tone={b.tone} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          {mark ? (
            <span aria-hidden style={{ width: 6, height: 6, borderRadius: "50%", background: mark, flex: "none" }} />
          ) : null}
          {toneWord ? <strong style={{ color: "var(--text-paper)" }}>{toneWord}</strong> : null}
          <span style={b.tone === "info" ? undefined : { color: "var(--text-paper)" }}>{figure}</span>
        </span>
      ) : null}
      {b.message || b.link ? (
        <span data-testid="llm-budget-note" data-status-line>
          {!figure && toneWord ? <strong style={{ color: "var(--text-paper)" }}>{`${toneWord} `}</strong> : null}
          {b.message}
          {b.link ? (
            <>
              {b.message ? " " : null}
              <a href={b.link.url} target="_blank" rel="noopener noreferrer">
                {b.link.label}
              </a>
            </>
          ) : null}
        </span>
      ) : null}
    </>
  );
}
