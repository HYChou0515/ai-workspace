/**
 * The people picker's order (docs/plan-people-picker-ranking.md D5).
 *
 * Compared top to bottom; a later key only breaks a tie in the earlier ones.
 *
 * | | nothing typed | text typed |
 * |---|---|---|
 * | 1 | related to this place | match quality |
 * | 2 | picked often (frecency) | related |
 * | 3 | section closeness | picked often |
 * | 4 | name | section closeness |
 * | 5 | | name |
 *
 * Before this the list had no order of its own — it was whatever order the
 * deployed directory (`UserDirectory.all_users`) lists people in — and any field
 * that contained the text matched equally.
 */

export type Person = { id: string; name: string; section: string; email: string };

export type RankContext = {
  /** What was typed; blank (or only spaces) means nothing was. */
  query: string;
  /** The viewer's own section, for closeness (D3). */
  viewerSection: string;
  /** People related to the place the picker is in (D4), e.g. a chat's speakers. */
  related?: readonly string[];
  /** id → how often and how recently it was picked (D2); absent ⇒ 0. */
  frecency?: ReadonlyMap<string, number>;
};

const collator = new Intl.Collator("zh-Hant-TW");

/** How well `person` matches `needle` (lower-cased, non-empty): 0 is best,
 * null is no match (D5: name starts > name contains > id/email starts > any
 * other field contains). */
function matchTier(person: Person, needle: string): number | null {
  const name = person.name.toLowerCase();
  if (name.startsWith(needle)) return 0;
  if (name.includes(needle)) return 1;
  const id = person.id.toLowerCase();
  const email = person.email.toLowerCase();
  if (id.startsWith(needle) || email.startsWith(needle)) return 2;
  if (id.includes(needle) || email.includes(needle) || person.section.toLowerCase().includes(needle)) return 3;
  return null;
}

/** How many leading characters `a` and `b` share (D3: a section code that
 * shares a longer head is usually a nearer part of the organisation). */
function sharedHead(a: string, b: string): number {
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a[i] === b[i]) i++;
  return i;
}

/** `people` that match `ctx.query`, best first. */
export function rankPeople<T extends Person>(people: readonly T[], ctx: RankContext): T[] {
  const needle = ctx.query.trim().toLowerCase();
  const related = new Set(ctx.related ?? []);
  const scored = [];
  for (const person of people) {
    const tier = needle ? matchTier(person, needle) : 0;
    if (tier === null) continue;
    scored.push({
      person,
      tier,
      related: related.has(person.id) ? 0 : 1,
      frecency: ctx.frecency?.get(person.id) ?? 0,
      closeness: ctx.viewerSection ? sharedHead(ctx.viewerSection, person.section) : 0,
    });
  }
  scored.sort(
    (a, b) =>
      a.tier - b.tier ||
      a.related - b.related ||
      b.frecency - a.frecency ||
      b.closeness - a.closeness ||
      collator.compare(a.person.name, b.person.name),
  );
  return scored.map((s) => s.person);
}
