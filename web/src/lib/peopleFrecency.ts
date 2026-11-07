/**
 * Who the signed-in person picks often, in this browser
 * (docs/plan-people-picker-ranking.md D2).
 *
 * "Often" is frecency, the way Firefox's address bar ranks: each of a person's
 * last 10 picks scores by how long ago it was — within 4 days 100, 14 days 70,
 * 31 days 50, 90 days 30, older 10 — and the scores add up. Someone picked every
 * week stays near the top through a quiet month; someone picked once yesterday
 * still shows up.
 *
 * Kept in `localStorage`, one key per signed-in person, at most 20 people. The
 * user chose the browser over the server knowing it does not follow them to
 * another machine. Every read and write tolerates a storage that throws (a
 * private window, blocked site data) or holds garbage: the picker then simply
 * has no "often" to rank by.
 */

const DAY = 24 * 60 * 60 * 1000;
const PICKS_PER_PERSON = 10;
const PEOPLE = 20;
/** [max age in days, score]; anything older scores the last value. */
const BANDS: readonly [number, number][] = [
  [4, 100],
  [14, 70],
  [31, 50],
  [90, 30],
];
const OLDEST = 10;

type Picks = Record<string, number[]>;

const keyOf = (userId: string) => `peoplePicker.frecency.${userId}`;

function scoreOf(times: readonly number[], now: number): number {
  let total = 0;
  for (const t of times) {
    const age = (now - t) / DAY;
    total += BANDS.find(([days]) => age <= days)?.[1] ?? OLDEST;
  }
  return total;
}

function load(userId: string, storage?: Storage): Picks {
  try {
    // Reached HERE, inside the guard: with site data blocked, merely reading
    // the `localStorage` property throws.
    const raw = (storage ?? localStorage).getItem(keyOf(userId));
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const out: Picks = {};
    for (const [id, times] of Object.entries(parsed as Record<string, unknown>)) {
      if (Array.isArray(times) && times.every((t) => typeof t === "number")) out[id] = times;
    }
    return out;
  } catch {
    return {};
  }
}

function save(userId: string, picks: Picks, storage?: Storage): void {
  try {
    (storage ?? localStorage).setItem(keyOf(userId), JSON.stringify(picks));
  } catch {
    // A full or blocked storage loses the memory, never the pick itself.
  }
}

/** id → frecency score at `now`, for everyone remembered. */
export function readFrecency(
  userId: string,
  now: number = Date.now(),
  storage?: Storage,
): Map<string, number> {
  const picks = load(userId, storage);
  return new Map(Object.entries(picks).map(([id, times]) => [id, scoreOf(times, now)]));
}

/** Remember that `personId` was just picked. */
export function recordPick(
  userId: string,
  personId: string,
  now: number = Date.now(),
  storage?: Storage,
): void {
  const picks = load(userId, storage);
  picks[personId] = [now, ...(picks[personId] ?? [])].slice(0, PICKS_PER_PERSON);
  const ids = Object.keys(picks);
  if (ids.length > PEOPLE) {
    // Forget whoever scores lowest now — never the person just picked.
    const ranked = ids
      .filter((id) => id !== personId)
      .sort((a, b) => scoreOf(picks[a], now) - scoreOf(picks[b], now));
    for (const id of ranked.slice(0, ids.length - PEOPLE)) delete picks[id];
  }
  save(userId, picks, storage);
}

/** Forget `personId` (the × beside a frequent person, D6). */
export function removePick(userId: string, personId: string, storage?: Storage): void {
  const picks = load(userId, storage);
  delete picks[personId];
  save(userId, picks, storage);
}
