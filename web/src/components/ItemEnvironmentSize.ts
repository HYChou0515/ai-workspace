/**
 * The size fields' wire grammar, on the client.
 *
 * `toSizeString` writes bytes the way the server's `parse_size` reads them —
 * an integer with the largest unit that divides it exactly (`"512M"`, `"2G"`,
 * or the bare byte count) — so a stated size round-trips: what the record
 * holds is what the field shows and what Save sends back.
 *
 * `cpuFault` / `memoryFault` are the server's refusals
 * (`api/item_routes.py:_validated_resources`, `quota/limits.py:parse_size`)
 * asked BEFORE the PUT: a 422 only says "not saved", and the person would be
 * left guessing at the grammar. Each answers with a `type` — `"unreadable"`
 * (not a number, not a size, zero) or `"over"` (past the ceiling) — because
 * the hint under the field teaches a different thing for each, and a `detail`
 * the hint interpolates. The ceiling is an argument, read from the record
 * (`max_cpu_cores` / `max_memory_bytes`, #830), never a number of this
 * module's own. `""` is valid in both — it means "use the default" and is
 * sent as `null`. `normaliseMemory` is what lets the memory field take the
 * spellings people actually use — "MB" as well as "M", a space, a fraction —
 * and still send the one the server parses.
 */

export function toSizeString(bytes: number): string;
export function toSizeString(bytes: number | null): string | null;
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

/**
 * Why a field's text would be refused, or `null` when it would not be.
 * `detail` is DATA for the hint, never copy: the record's ceiling in the
 * server's spelling for `"over"`, the grammar's examples for `"unreadable"`
 * (joined with ` / ` so either locale can read them). The sentence around it
 * belongs to the panel's `useT`; this module has no locale.
 */
export type SizeFault = { type: "unreadable" | "over"; detail: string } | null;

const CPU_EXAMPLES = "1 / 0.5";

/** More than 0 and finite (the server refuses 0 rather than reading it as
 *  "unlimited"), and no larger than `max` — the server's `_within`, which is
 *  `0 < value <= ceiling`. `max` is the RECORD's (#830), never a number held
 *  here: a copy of the server's `1024` would keep teaching it after the
 *  server moved, with every test still green. */
export function cpuFault(text: string, max: number): SizeFault {
  if (text === "") return null;
  const n = Number(text);
  if (!Number.isFinite(n) || n <= 0) return { type: "unreadable", detail: CPU_EXAMPLES };
  return n <= max ? null : { type: "over", detail: String(max) };
}

const UNITS: Record<string, number> = { K: 1024, M: 1024 ** 2, G: 1024 ** 3, T: 1024 ** 4 };

/**
 * What a person writes → what `parse_size` reads, or `null` when it is not a
 * size at all. People write "512MB", "512 mb", "1.5G" and the display format
 * "512.0 MB" as readily as "512M"; the server reads only `<integer>[K|M|G|T]`.
 * So: any case, an optional space, an optional trailing B, full-width digits,
 * a fraction WITH a unit folded into the exact smaller unit ("1.5G" →
 * "1536M"; below a whole byte it rounds), a fraction WITHOUT one refused (a
 * fraction of a byte is not a size). `""` is "the default", not a size — the
 * caller sends `null` for it.
 */
export function normaliseMemory(text: string): string | null {
  // Full-width digits (a zh-TW IME slip) are digits; the server's
  // `str.isdigit` reads them too.
  const ascii = text.replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));
  const m = /^\s*(\d+(?:\.\d+)?)\s*(?:([kmgt])b?)?\s*$/i.exec(ascii);
  if (!m) return null;
  // A fraction is fine WITH a unit (1.5G is 1536M); without one it would be
  // a fraction of a byte, which is not a size.
  if (!m[2] && m[1].includes(".")) return null;
  const unit = m[2] ? UNITS[m[2].toUpperCase()]! : 1;
  const bytes = Math.round(Number(m[1]) * unit);
  if (!(bytes > 0)) return null;
  return toSizeString(bytes) ?? null;
}

const MEMORY_EXAMPLES = "512M / 512MB / 1.5G";

/** Empty = the default; otherwise something `normaliseMemory` can read, no
 *  larger than `max` (the RECORD's ceiling, #830). The server refuses zero on
 *  this route, unlike the operator's config. Compared in bytes, read back
 *  from the spelling that would be sent — so what is refused here is what
 *  the server would refuse on the wire. */
export function memoryFault(text: string, max: number): SizeFault {
  if (text.trim() === "") return null;
  const bytes = parseSize(normaliseMemory(text));
  if (bytes === null) return { type: "unreadable", detail: MEMORY_EXAMPLES };
  return bytes <= max ? null : { type: "over", detail: toSizeString(max) };
}

/** The server's spelling back to bytes — for writing a just-sent size into
 *  the cached record when its re-read failed. Only ever fed what
 *  `normaliseMemory` produced. */
export function parseSize(text: string | null): number | null {
  if (text === null) return null;
  const m = /^(\d+)([KMGT])?$/.exec(text);
  if (!m) return null;
  return Number(m[1]) * (m[2] ? UNITS[m[2]]! : 1);
}
