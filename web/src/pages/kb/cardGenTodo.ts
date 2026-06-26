/**
 * #175 — serialize / parse the "todo.md" bulk-edit view of card proposals.
 *
 * The review surface offers two views of the same proposals (grill Q3): a
 * structured per-card list AND one editable markdown document. The markdown is a
 * sequence of `<!-- card N [mode] keys: … -->` blocks; the body after each marker
 * is the only thing todo.md edits (title / keys / decisions stay in list mode),
 * so the round-trip maps each block's body back onto the proposal at index N.
 */
import type { KbProposedCard } from "../../api/kb";

const marker = (i: number, p: KbProposedCard) =>
  `<!-- card ${i} [${p.mode}] keys: ${p.keys.join(", ")} -->`;

export function serializeTodo(proposals: KbProposedCard[]): string {
  return proposals
    .map((p, i) => {
      const flag = p.confident ? "" : "⚠️ uncertain — ";
      const heading = p.title || p.keys[0] || "";
      return `${marker(i, p)}\n# ${heading}\n\n${flag}${p.body}`.replace(/\s+$/, "");
    })
    .join("\n\n");
}

const MARKER_RE = /^<!--\s*card\s+(\d+)\s*\[(?:new|update)\]\s*keys:.*-->$/;

export function parseTodo(md: string, base: KbProposedCard[]): KbProposedCard[] {
  // Collect each marked block's body lines, keyed by the marker's index.
  const bodies = new Map<number, string[]>();
  let cur: number | null = null;
  for (const line of md.split("\n")) {
    const m = line.match(MARKER_RE);
    if (m) {
      cur = Number(m[1]);
      bodies.set(cur, []);
      continue;
    }
    if (cur !== null) bodies.get(cur)?.push(line);
  }
  return base.map((p, i) => {
    const raw = bodies.get(i);
    if (raw === undefined) return p; // block removed → leave the proposal untouched
    const text = raw
      .join("\n")
      .trim()
      .replace(/^#[^\n]*\n+/, "") // drop the "# heading" line
      .replace(/^⚠️ uncertain — /, "") // drop the uncertainty prefix
      .trim();
    return { ...p, body: text };
  });
}
