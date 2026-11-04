// What a person told the skill-update reminder (docs/plan-skill-hub-release-notes
// D3 / D4): "remind me later" — 7 days, fixed — or "skip this version". Kept
// in this browser, per person + entry + version, so a newer version reminds
// again from the start and someone else in the same browser is still told.
// Callers judge only once the current user is KNOWN: on a cold load the id is
// the "default-user" placeholder, and judging on it re-showed a reminder a
// person had answered (the onboarding bug, #997).

const KEY = "skillHub.updateReminders";

/** "Remind me later" — fixed; the person cannot change it (D3). */
export const LATER_MS = 7 * 24 * 3600 * 1000;

type Answer = { until?: number; snoozed?: number; skipped?: boolean };
type Store = Record<string, Answer>;

export type Reminder = {
  /** Remind now: never answered, or its Later has run out. */
  due: boolean;
  /** Offer "skip this version": from the second reminder on (D3). */
  canSkip: boolean;
  skipped: boolean;
  /** While a Later runs: when it ends (epoch ms); `null` otherwise. */
  until: number | null;
};

// The answers of this page when the browser's store refused a write (a
// private window, a full quota): without it Later / Skip were recorded nowhere
// and looked like buttons that do nothing. Until the next successful write.
let unsaved: Store | null = null;

function read(): Store {
  if (unsaved) return { ...unsaved };
  try {
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" ? (parsed as Store) : {};
  } catch {
    return {};
  }
}

const CHANGED = "skillHub.updateReminders.changed";

function write(store: Store): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(store));
    unsaved = null;
  } catch {
    // A full or blocked store: kept for this page, so the answer holds until
    // it is closed — and the reminder comes back on the next visit.
    unsaved = store;
  }
  // Every reader of an answer (the panel's row, the skills button's mark)
  // re-reads at once.
  window.dispatchEvent(new Event(CHANGED));
}

/** Call `listener` whenever a Later or a Skip is recorded — here, or in another
 * tab of this browser (its `storage` event); returns the unsubscribe. */
export function onRemindersChanged(listener: () => void): () => void {
  const fromAnotherTab = (e: StorageEvent) => {
    if (e.key === KEY || e.key === null) listener();
  };
  window.addEventListener(CHANGED, listener);
  window.addEventListener("storage", fromAnotherTab);
  return () => {
    window.removeEventListener(CHANGED, listener);
    window.removeEventListener("storage", fromAnotherTab);
  };
}

// encodeURIComponent so a ":" in any part can never make two keys collide.
const keyOf = (user: string, entry: string, version: number) =>
  [user, entry, String(version)].map(encodeURIComponent).join(":");

export function reminderOf(user: string, entry: string, version: number, now: number): Reminder {
  const a = read()[keyOf(user, entry, version)] ?? {};
  if (a.skipped) return { due: false, canSkip: true, skipped: true, until: null };
  if (a.until !== undefined && now < a.until) {
    return { due: false, canSkip: (a.snoozed ?? 0) > 0, skipped: false, until: a.until };
  }
  return { due: true, canSkip: (a.snoozed ?? 0) > 0, skipped: false, until: null };
}

export function remindLater(user: string, entry: string, version: number, now: number): void {
  const store = read();
  const key = keyOf(user, entry, version);
  const a = store[key] ?? {};
  store[key] = { ...a, until: now + LATER_MS, snoozed: (a.snoozed ?? 0) + 1 };
  write(store);
}

export function skipVersion(user: string, entry: string, version: number): void {
  const store = read();
  const key = keyOf(user, entry, version);
  store[key] = { ...(store[key] ?? {}), skipped: true };
  write(store);
}
