/**
 * The size fields' wire grammar, on the client.
 *
 * `toSizeString` writes bytes the way the server's `parse_size` reads them —
 * an integer with the largest unit that divides it exactly (`"512M"`, `"2G"`,
 * or the bare byte count) — so a stated size round-trips: what the record
 * holds is what the field shows and what Save sends back.
 *
 * `isValidCpu` / `isValidMemory` are the server's refusals
 * (`api/item_routes.py:_validated_resources`, `quota/limits.py:parse_size`)
 * asked BEFORE the PUT: a 422 only says "not saved", and the person would be
 * left guessing at the grammar. `""` is valid in both — it means "use the
 * default" and is sent as `null`.
 */

export function toSizeString(bytes: number | null): string | null {
  if (bytes === null) return null;
  for (const [unit, size] of [
    ["G", 1024 ** 3],
    ["M", 1024 ** 2],
    ["K", 1024],
  ] as const) {
    if (bytes >= size && bytes % size === 0) return `${bytes / size}${unit}`;
  }
  return String(bytes);
}

/** More than 0 and finite; the server refuses 0 rather than reading it as "unlimited". */
export function isValidCpu(text: string): boolean {
  if (text === "") return true;
  const n = Number(text);
  return Number.isFinite(n) && n > 0;
}

/** `parse_size`'s grammar: digits with an optional K/M/G/T, either case, and
 *  not zero (which this route refuses, unlike the operator's config). */
export function isValidMemory(text: string): boolean {
  const trimmed = text.trim();
  if (trimmed === "") return true;
  const m = /^(\d+)([kmgt])?$/i.exec(trimmed);
  return m !== null && Number(m[1]) > 0;
}
