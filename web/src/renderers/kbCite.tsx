/**
 * Shared `[n]` → KB citation rendering, used by every surface that shows an
 * answer carrying KB citations: the report markdown (`ReportRenderer`), the
 * chat answer markdown, and the `ask_knowledge_base` tool card's plain-text
 * body (`AgentEntryView`). Each surface resolves the same `[n]` marker against
 * the message's `citations` and renders it as a clickable affordance that opens
 * the cited document — keeping the byMarker / muted / multi-match rules in ONE
 * place so they stay identical everywhere.
 */

import type { ReactElement, ReactNode } from "react";
import { defaultUrlTransform } from "react-markdown";

import type { MessageCitation } from "../api/types";

/**
 * Group a turn's citations by their `[n]` marker. A single marker can map to
 * several chunks (the agent re-used `[5]` across calls / documents), so each
 * entry is a list, preserving citation order.
 */
export function buildByMarker(
  citations: readonly MessageCitation[],
): Map<number, MessageCitation[]> {
  const m = new Map<number, MessageCitation[]>();
  for (const c of citations) {
    const list = m.get(c.marker);
    if (list) list.push(c);
    else m.set(c.marker, [c]);
  }
  return m;
}

const CITE_HREF = "kb-cite:";

/**
 * react-markdown `urlTransform`. The default sanitizer drops links with an
 * unknown scheme (anything but http/https/mailto/…) to `''`, which would strip
 * the `kb-cite:N` hrefs `remarkKbCitation` emits before our `a` handler ever
 * sees them. Preserve those; defer to the default for every other URL so
 * ordinary link sanitizing is unchanged.
 */
export function kbCiteUrlTransform(url: string): string {
  return url.startsWith(CITE_HREF) ? url : defaultUrlTransform(url);
}

/** The hover tooltip listing every chunk a marker maps to, one per line. */
function citeTitle(matches: readonly MessageCitation[]): string {
  return matches.map((c) => `${c.filename} — ${c.snippet}`).join("\n");
}

/**
 * The `a`-slot handler for a markdown renderer whose `[N]` markers were turned
 * into `kb-cite:N` links by `remarkKbCitation`. Returns:
 *  - a clickable inline pill (opens the FIRST matching chunk; tooltip lists all)
 *    when the marker resolves against `byMarker`;
 *  - a muted, non-clickable span keeping the literal `[N]` when the marker has
 *    no citation in this turn's pool;
 *  - `null` when `href` is not a `kb-cite:` link, so the caller renders it as an
 *    ordinary markdown link.
 */
export function kbCiteAnchor(
  { href, children }: { href?: string; children?: ReactNode },
  byMarker: Map<number, MessageCitation[]>,
  onOpen?: (c: MessageCitation) => void,
): ReactElement | null {
  if (typeof href !== "string" || !href.startsWith(CITE_HREF)) return null;
  const marker = Number.parseInt(href.slice(CITE_HREF.length), 10);
  const matches = byMarker.get(marker);
  if (!matches || matches.length === 0) {
    return (
      <span className="kb-cite-inline" style={{ cursor: "default", opacity: 0.55 }}>
        {children}
      </span>
    );
  }
  return (
    <button
      type="button"
      className="kb-cite-inline"
      title={citeTitle(matches)}
      onClick={() => onOpen?.(matches[0])}
    >
      {children}
    </button>
  );
}

const MARKER_RE = /\[(\d+)\]/g;

/**
 * Split a PLAIN-TEXT body (e.g. the `ask_knowledge_base` tool card's `<pre>`)
 * into text runs + clickable `[n]` affordances, for callers that can't run it
 * through markdown. A matched marker becomes a restrained inline control
 * (`kb-cite-pre` — accent text only, no pill chrome, so the monospace raw
 * output keeps its look); an unmatched marker stays verbatim in the prose.
 * The body round-trips character-for-character when read as text.
 */
export function renderCitedText(
  text: string,
  byMarker: Map<number, MessageCitation[]>,
  onOpen?: (c: MessageCitation) => void,
): ReactNode {
  const out: ReactNode[] = [];
  let lastIdx = 0;
  let key = 0;
  MARKER_RE.lastIndex = 0;
  for (let m = MARKER_RE.exec(text); m !== null; m = MARKER_RE.exec(text)) {
    const matches = byMarker.get(Number.parseInt(m[1], 10));
    // Unmatched marker: leave it in the surrounding text run, so the literal
    // `[n]` stays exactly where the agent wrote it.
    if (!matches || matches.length === 0) continue;
    if (m.index > lastIdx) out.push(text.slice(lastIdx, m.index));
    out.push(
      <button
        key={key}
        type="button"
        className="kb-cite-pre"
        title={citeTitle(matches)}
        onClick={() => onOpen?.(matches[0])}
      >
        {m[0]}
      </button>,
    );
    key += 1;
    lastIdx = MARKER_RE.lastIndex;
  }
  if (lastIdx < text.length) out.push(text.slice(lastIdx));
  return out;
}
