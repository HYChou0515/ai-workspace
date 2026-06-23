// Per-user dismissal store for the versioned welcome teaching (#161). The modal
// auto-shows until the user permanently dismisses the *current* version; bumping
// the content version makes it eligible to show again. State is keyed by
// user + scope ("platform" or an app slug) → the version they dismissed.

const KEY = "onboarding.dismissed";

type Store = Record<string, string>;

function read(): Store {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" ? (parsed as Store) : {};
  } catch {
    return {};
  }
}

function write(s: Store): void {
  localStorage.setItem(KEY, JSON.stringify(s));
}

// encodeURIComponent escapes ":" in the userId so it can never collide with the
// scope separator (a userId of "a:b" stays distinct from user "a" / scope "b:…").
function scopeKey(userId: string, scope: string): string {
  return `${encodeURIComponent(userId)}:${scope}`;
}

/** The version this user permanently dismissed for `scope`, or null. */
export function dismissedVersion(userId: string, scope: string): string | null {
  return read()[scopeKey(userId, scope)] ?? null;
}

/** True when this exact `version` was permanently dismissed by this user+scope. */
export function isDismissed(userId: string, scope: string, version: string): boolean {
  return dismissedVersion(userId, scope) === version;
}

/** Permanently dismiss this `version` for this user+scope ("Don't show again"). */
export function dismiss(userId: string, scope: string, version: string): void {
  const s = read();
  s[scopeKey(userId, scope)] = version;
  write(s);
}
