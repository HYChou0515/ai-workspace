/**
 * A tiny rehype plugin that wraps the first occurrence of `snippet` in the
 * rendered document with <mark class="kb-hl">, so a citation's passage is
 * highlighted in place. Operates on hast text nodes: if the snippet falls
 * within one text node it's highlighted; if it straddles markdown formatting
 * (bold, a link, …) it's left alone — the cited-passage callout still shows it.
 */

type HastNode = {
  type: string;
  value?: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

export function rehypeHighlightSnippet(snippet: string) {
  const needle = snippet.trim();
  return (tree: HastNode): void => {
    if (!needle) return;
    let done = false;

    const visit = (node: HastNode): void => {
      if (done || !node.children) return;
      const next: HastNode[] = [];
      for (const child of node.children) {
        if (done) {
          next.push(child);
          continue;
        }
        const at = child.type === "text" ? (child.value ?? "").indexOf(needle) : -1;
        if (at === -1) {
          visit(child);
          next.push(child);
          continue;
        }
        const text = child.value ?? "";
        if (at > 0) next.push({ type: "text", value: text.slice(0, at) });
        next.push({
          type: "element",
          tagName: "mark",
          properties: { className: ["kb-hl"] },
          children: [{ type: "text", value: needle }],
        });
        const rest = text.slice(at + needle.length);
        if (rest) next.push({ type: "text", value: rest });
        done = true;
      }
      node.children = next;
    };

    visit(tree);
  };
}
