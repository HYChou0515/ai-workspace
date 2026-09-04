/**
 * Structural equality for "is this modal dirty?" (#779).
 *
 * `JSON.stringify(a) !== JSON.stringify(b)` is the obvious way to ask and it is
 * wrong twice over for the shapes these dialogs hold:
 *
 *  - **Key and element order.** A sparse override deletes and re-adds keys as
 *    the user toggles, and a grant list reorders when a subject is removed and
 *    re-added. Both restore the same MEANING with a different string, so the
 *    modal would claim unsaved work when nothing changed. That direction is the
 *    dangerous one: a prompt that fires over nothing is one people learn to
 *    dismiss without reading, and then it is worth less than no prompt at all.
 *
 *  - **Sets.** `JSON.stringify(new Set(["read"]))` is `{}` — every Set, however
 *    full, serialises identically. `ItemGrant.verbs` is a Set, so a whole custom
 *    permission edit read as "unchanged" and closed without asking: silent loss,
 *    the failure this guard exists to prevent.
 *
 * Order-insensitive by construction: everything is rendered to a canonical
 * string with object keys and array elements sorted.
 */
function canonical(value: unknown): string {
  if (value instanceof Set) return `Set(${[...value].map(canonical).sort().join(",")})`;
  if (value instanceof Map) {
    return `Map(${[...value].map(([k, v]) => `${canonical(k)}:${canonical(v)}`).sort().join(",")})`;
  }
  if (Array.isArray(value)) return `[${value.map(canonical).sort().join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`)
      .sort();
    return `{${entries.join(",")}}`;
  }
  // `undefined` has no JSON form; keep it distinct from null rather than losing
  // the difference between "absent" and "explicitly empty".
  return value === undefined ? "undefined" : JSON.stringify(value);
}

export function sameShape(a: unknown, b: unknown): boolean {
  return canonical(a) === canonical(b);
}
