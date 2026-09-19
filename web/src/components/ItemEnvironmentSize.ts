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
 * default" and is sent as `null`. `normaliseMemory` is what lets the memory
 * field take the spellings people actually use — "MB" as well as "M", a
 * space, a fraction — and still send the one the server parses.
 */

export function toSizeString(bytes: number | null): string | null {
  if (bytes === null) return null;
  for (const [unit, size] of [
    ["T", 1024 ** 4],
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

const UNITS: Record<string, number> = { K: 1024, M: 1024 ** 2, G: 1024 ** 3, T: 1024 ** 4 };

/**
 * What a person writes → what `parse_size` reads, or `null` when it is not a
 * size at all. People write "512MB", "512 mb", "1.5G" and the display format
 * "512.0 MB" as readily as "512M"; the server reads only `<integer>[K|M|G|T]`.
 * So: any case, an optional space, an optional trailing B, a fraction folded
 * into the exact smaller unit ("1.5G" → "1536M"; a fraction of a byte rounds).
 * `""` is "the default", not a size — the caller sends `null` for it.
 */
export function normaliseMemory(text: string): string | null {
  const m = /^\s*(\d+(?:\.\d+)?)\s*(?:([kmgt])b?)?\s*$/i.exec(text);
  if (!m) return null;
  // A fraction is fine WITH a unit (1.5G is 1536M); without one it would be
  // a fraction of a byte, which is not a size.
  if (!m[2] && m[1].includes(".")) return null;
  const unit = m[2] ? UNITS[m[2].toUpperCase()]! : 1;
  const bytes = Math.round(Number(m[1]) * unit);
  if (!(bytes > 0)) return null;
  return toSizeString(bytes) ?? null;
}

/** Empty = the default; otherwise something `normaliseMemory` can read. The
 *  server refuses zero on this route, unlike the operator's config. */
export function isValidMemory(text: string): boolean {
  return text.trim() === "" || normaliseMemory(text) !== null;
}
