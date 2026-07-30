/**
 * #615 P1 — the work calendar's override list as editable text.
 *
 * One date per line, `YYYY-MM-DD=off|work`. `off` frees a working day (a public
 * holiday); `work` claims a non-working one (a make-up workday, which is why
 * this is a calendar and not a weekend toggle).
 *
 * A line that cannot be read is REPORTED, never silently dropped: quietly
 * skipping a typo would leave someone believing a holiday is recorded when it
 * is not, and they would only find out when an agent worked through it.
 */

export type OverrideValue = "off" | "work";
export type OverrideMap = Record<string, OverrideValue>;

const OVERRIDE_VALUES: OverrideValue[] = ["off", "work"];

function isRealDate(text: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return false;
  const parsed = new Date(`${text}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === text;
}

/** Parse the editor's text. Returns the entries it understood plus a message
 * for every line it did not — both halves are shown to the editing user. */
export function parseOverrides(text: string): { overrides: OverrideMap; errors: string[] } {
  const overrides: OverrideMap = {};
  const errors: string[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const [datePart, ...rest] = line.split("=");
    const day = datePart.trim();
    const value = rest.join("=").trim();
    if (rest.length === 0) {
      errors.push(`“${line}” — write it as YYYY-MM-DD=off or YYYY-MM-DD=work`);
      continue;
    }
    if (!isRealDate(day)) {
      errors.push(`“${day}” is not a real date (YYYY-MM-DD)`);
      continue;
    }
    if (!OVERRIDE_VALUES.includes(value as OverrideValue)) {
      errors.push(`“${value}” on ${day} — use off (a holiday) or work (a make-up workday)`);
      continue;
    }
    overrides[day] = value as OverrideValue;
  }
  return { overrides, errors };
}

/** Render stored overrides back into editor text, oldest date first. */
export function formatOverrides(overrides: Record<string, string>): string {
  return Object.keys(overrides)
    .sort()
    .map((day) => `${day}=${overrides[day]}`)
    .join("\n");
}
