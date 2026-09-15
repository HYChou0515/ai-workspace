/**
 * The document-tree order (plan-rag-context P2) — ONE rule, stated to users,
 * shared with the backend.
 *
 *     The order is the document tree read top to bottom.
 *
 * Precisely: at each path level, directories sort before files; among siblings,
 * maximal ASCII-digit runs compare by numeric value (`9` < `10`), everything
 * else by Unicode code point after lower-casing, with the raw string as the
 * final tiebreak. CJK numerals and full-width digits are NOT numeric — the
 * user-facing rule is "prefix filenames with Arabic numerals to control order".
 *
 * Locale-independent by construction: no `localeCompare`, no `Intl.Collator`.
 * An order that depends on the machine is not well-defined, and this repo has
 * already been bitten by CI's Node locale differing from local. Three places
 * where "the obvious JS" would silently diverge from the Python
 * (`kb/tree_order.py`): digit runs are compared as BigInt (Python ints are
 * unbounded), text is compared by CODE POINT (`<` on strings compares UTF-16
 * code units, which misorders Ext-B CJK), and case is folded with
 * `toLowerCase` (the same Unicode default mapping as Python's `str.lower`).
 *
 * Both test suites assert `tests/kb/tree_order.golden.json`, so the two
 * implementations cannot drift silently.
 */

const RUNS = /[0-9]+|[^0-9]+/g;

type Run = { tag: 0; value: bigint; raw: string } | { tag: 1; value: string; raw: string };

function runsOf(name: string): Run[] {
  const out: Run[] = [];
  for (const run of name.match(RUNS) ?? []) {
    out.push(
      /^[0-9]/.test(run)
        ? { tag: 0, value: BigInt(run), raw: run }
        : { tag: 1, value: run.toLowerCase(), raw: run },
    );
  }
  return out;
}

/** Code-point order, the same total order Python's `str` comparison gives. */
function compareCodePoints(a: string, b: string): number {
  const ia = a[Symbol.iterator]();
  const ib = b[Symbol.iterator]();
  for (;;) {
    const na = ia.next();
    const nb = ib.next();
    if (na.done && nb.done) return 0;
    if (na.done) return -1;
    if (nb.done) return 1;
    const ca = na.value.codePointAt(0)!;
    const cb = nb.value.codePointAt(0)!;
    if (ca !== cb) return ca < cb ? -1 : 1;
  }
}

/** Natural order of two sibling NAMES (no directory rule — see compareSiblings). */
export function compareNames(a: string, b: string): number {
  const ra = runsOf(a);
  const rb = runsOf(b);
  const n = Math.min(ra.length, rb.length);
  for (let i = 0; i < n; i++) {
    const x = ra[i]!;
    const y = rb[i]!;
    if (x.tag !== y.tag) return x.tag - y.tag; // a digit run precedes a text run
    let c: number;
    if (x.tag === 0 && y.tag === 0) {
      c = x.value < y.value ? -1 : x.value > y.value ? 1 : 0;
    } else {
      c = compareCodePoints(x.value as string, y.value as string);
    }
    if (c !== 0) return c;
    c = compareCodePoints(x.raw, y.raw);
    if (c !== 0) return c;
  }
  if (ra.length !== rb.length) return ra.length - rb.length;
  return compareCodePoints(a, b);
}

/** The whole rule for two siblings: a directory before a file, then by name. */
export function compareSiblings(
  a: { name: string; isDir: boolean },
  b: { name: string; isDir: boolean },
): number {
  if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
  return compareNames(a.name, b.name);
}

/**
 * The same rule over two slash-separated paths — what the backend sorts and
 * what the golden fixture is written in. A segment with segments after it is a
 * directory. Built on `compareSiblings` so there is one rule, not two.
 */
export function compareTreePaths(a: string, b: string): number {
  const sa = a.split("/");
  const sb = b.split("/");
  const n = Math.min(sa.length, sb.length);
  for (let i = 0; i < n; i++) {
    const c = compareSiblings(
      { name: sa[i]!, isDir: i < sa.length - 1 },
      { name: sb[i]!, isDir: i < sb.length - 1 },
    );
    if (c !== 0) return c;
  }
  return sa.length - sb.length;
}
