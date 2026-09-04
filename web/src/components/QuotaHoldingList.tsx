/**
 * The closable half of an "at your environment limit" refusal.
 *
 * The existing wording points at `/my-resources`, which is a page the person has
 * to notice, navigate to, act on, and navigate back from — acceptable while
 * hitting the limit was exceptional. It is not, since an item defaults to its
 * App's ceiling: the wall is now an ordinary part of using the product, so its
 * exit belongs where the refusal is.
 *
 * Renders NOTHING for an empty list, and empty has three innocent causes: the
 * refusal was not about environments, the viewer is not the owner (so the
 * backend withheld the inventory), or the body predates the field. None is an
 * error, and none should produce a spinner or an apology.
 */

import type { QuotaHolder } from "../lib/quotaHolding";
import { useT } from "../lib/i18n";

export function QuotaHoldingList({
  holding,
  onClose,
  busyItemId,
}: {
  holding: QuotaHolder[];
  onClose: (itemId: string) => void;
  /** The row whose close is in flight — disabled so a second click cannot
   *  queue a second teardown of the same sandbox. */
  busyItemId?: string;
}) {
  const t = useT();
  if (holding.length === 0) return null;
  return (
    <ul className="quota-holding">
      {holding.map((h) => (
        <li key={h.itemId}>
          {/* An unresolved title falls back to the id: a deleted item still
              holds its environment until it is reaped, and a blank row is one
              nobody can act on. */}
          <span className="holding-title">{h.title || h.itemId}</span>
          <span data-testid="holding-cpu" className="holding-cpu">
            {h.cpuCores}
          </span>
          <button
            type="button"
            data-testid="holding-close"
            disabled={busyItemId === h.itemId}
            onClick={() => onClose(h.itemId)}
          >
            {t("itemenv.close")}
          </button>
        </li>
      ))}
    </ul>
  );
}
