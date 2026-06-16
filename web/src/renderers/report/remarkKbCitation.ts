/**
 * remark plugin — split markdown text nodes containing `[N]` KB citation
 * markers into text + link pairs. The link's url is `kb-cite:N`, which the
 * ReportRenderer's custom `a` component intercepts to render as a clickable
 * inline pill that opens the cited KB document.
 *
 * The plugin runs in-place over mdast and only touches `text` nodes; existing
 * markdown links, code blocks, and emphasis pass through unchanged. We don't
 * pull `unist-util-visit` (would add a runtime dep) — a tiny recursive walker
 * is enough and keeps this self-contained.
 *
 * The N → Citation lookup itself happens in the React component, not here —
 * the plugin only emits the syntactic anchor (`kb-cite:N` href). That keeps
 * this purely-syntactic step independent from the conversation state that
 * resolves the marker.
 */

// Using `any` over the mdast types so we don't take a hard dep on @types/mdast
// for a 30-line plugin. The shapes asserted here are stable across remark
// versions (text/value, link/url/children).
// biome-ignore-file lint/suspicious/noExplicitAny: see comment above

type MdNode = {
  type: string;
  value?: string;
  children?: MdNode[];
  // remark widens these further per-type; we only set the link fields we need.
  url?: string;
  title?: string | null;
};

const MARKER_RE = /\[(\d+)\]/g;

function splitTextWithCitations(text: string): MdNode[] {
  const out: MdNode[] = [];
  let lastIdx = 0;
  // Reset stateful regex between calls.
  MARKER_RE.lastIndex = 0;
  for (let m = MARKER_RE.exec(text); m !== null; m = MARKER_RE.exec(text)) {
    if (m.index > lastIdx) {
      out.push({ type: "text", value: text.slice(lastIdx, m.index) });
    }
    out.push({
      type: "link",
      url: `kb-cite:${m[1]}`,
      title: null,
      children: [{ type: "text", value: m[0] }],
    });
    lastIdx = MARKER_RE.lastIndex;
  }
  if (out.length === 0) return [{ type: "text", value: text }];
  if (lastIdx < text.length) out.push({ type: "text", value: text.slice(lastIdx) });
  return out;
}

function walk(node: MdNode): void {
  if (!node.children || node.children.length === 0) return;
  // Skip code / inline-code subtrees: `[1]` inside a code span is verbatim,
  // not a citation marker. (remark's `code`/`inlineCode` nodes don't have
  // a `children` array, so they're naturally skipped — guard anyway.)
  if (node.type === "code" || node.type === "inlineCode") return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    if (child.type === "text" && typeof child.value === "string") {
      next.push(...splitTextWithCitations(child.value));
      continue;
    }
    walk(child);
    next.push(child);
  }
  node.children = next;
}

/**
 * The remark plugin. Returns the standard `(tree) => void` transformer
 * remark-plugins-format expects.
 */
export function remarkKbCitation() {
  return (tree: MdNode) => {
    walk(tree);
  };
}
