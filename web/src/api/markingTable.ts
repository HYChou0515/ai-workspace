/**
 * P7 (plan-view-plugins-pr5-finish): save a marking's lit rows as a new CSV in
 * the item's workspace. The route selects the rows in the sandbox (the view's
 * source, its transforms, the platform's one lighting rule) and writes them
 * through the file facade — so the quota and the caller's permission apply.
 */
import { apiFetch, detailSentence } from "./http";

const enc = encodeURIComponent;

export type SaveMarkingTableBody = {
  name: string;
  /** The view the rows come from: a view file, or a table file itself. */
  view: string;
  /** The marking's values; `null` → the route reads the marking the chat
   * send wrote (the chip holds only counts). */
  columns: Record<string, string[]> | null;
  /** `yyyymmdd-hhmm` in the saver's own clock — the name they will look for. */
  stamp: string;
  /** The chip's `SentMarking.digest` (what its message sent); `null` from the
   * header, which sends the values themselves. */
  digest: string | null;
};

export type SavedMarkingTable = { path: string; rows: number };

/** Thrown with the route's own sentence, ready to show as it is. */
export class SaveTableRefused extends Error {}

export async function saveMarkingTable(
  slug: string,
  itemId: string,
  body: SaveMarkingTableBody,
): Promise<SavedMarkingTable> {
  const resp = await apiFetch(`/a/${enc(slug)}/items/${enc(itemId)}/markings/table`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new SaveTableRefused((await detailSentence(resp)) ?? "");
  return (await resp.json()) as SavedMarkingTable;
}

/** `yyyymmdd-hhmm` for `now`, in local time. */
export function tableStamp(now: Date): string {
  const two = (n: number) => String(n).padStart(2, "0");
  return (
    `${now.getFullYear()}${two(now.getMonth() + 1)}${two(now.getDate())}` +
    `-${two(now.getHours())}${two(now.getMinutes())}`
  );
}
