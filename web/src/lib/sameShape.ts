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
 *
 * THE LIMIT THAT COMES WITH THAT: arrays are compared as SETS. `[a, b]` equals
 * `[b, a]`. That is right for every caller today — grant lists, group grants,
 * and `ItemForm`'s tag fields, whose input only appends and de-duplicates, so a
 * user cannot reorder one on purpose. It would be WRONG for an array whose order
 * the user arranges deliberately (a ranked list, an ordered pipeline): a
 * reordering would read as no change and close without asking. Don't reach for
 * this on one of those — the failure is silent, which is the direction that
 * matters.
 */
function canonical(value: unknown): string {
  if (value instanceof Set) return `Set(${[...value].map(canonical).sort().join(",")})`;
  if (value instanceof Map) {
    return `Map(${[...value].map(([k, v]) => `${canonical(k)}:${canonical(v)}`).sort().join(",")})`;
  }
  if (Array.isArray(value)) return `[${value.map(canonical).sort().join(",")}]`;
  if (value && typeof value === "object") {
    // A plain-object walk reproduces the very bug this file exists to fix:
    // `Object.entries(new Date())` is `[]`, so every Date — and URL, RegExp, any
    // class instance without own enumerable properties — would canonicalise to
    // `{}` and compare equal to each other and to `{}`. Nothing here holds one
    // today, but this is the project's dirty comparator now, and the first modal
    // to snapshot a date field would read every change as unchanged and close
    // without asking.
    //
    // Anything carrying its own primitive identity is compared by that. What
    // remains — an unrecognised class instance — THROWS rather than silently
    // collapsing: a loud failure at the call site is recoverable, a modal that
    // quietly stops noticing edits is not.
    const proto = Object.getPrototypeOf(value);
    if (proto !== Object.prototype && proto !== null) {
      if (value instanceof Date) return `Date(${value.getTime()})`;
      if (value instanceof RegExp) return `RegExp(${String(value)})`;
      if (value instanceof URL) return `URL(${value.href})`;
      throw new Error(
        `sameShape: no canonical form for ${value.constructor?.name ?? "this object"} — ` +
          "add one rather than letting it compare equal to everything else.",
      );
    }
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
