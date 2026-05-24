import { describe, expect, it } from "vitest";

import { rehypeHighlightSnippet } from "./rehypeHighlightSnippet";

type N = {
  type: string;
  value?: string;
  tagName?: string;
  children?: N[];
  properties?: Record<string, unknown>;
};

function paragraph(text: string): N {
  return { type: "root", children: [{ type: "element", tagName: "p", children: [{ type: "text", value: text }] }] };
}

function marks(tree: N): N[] {
  const found: N[] = [];
  const walk = (n: N) => {
    if (n.tagName === "mark") found.push(n);
    n.children?.forEach(walk);
  };
  walk(tree);
  return found;
}

describe("rehypeHighlightSnippet", () => {
  it("wraps the first occurrence of the snippet in a <mark>", () => {
    const tree = paragraph("before zone three drift after");
    rehypeHighlightSnippet("zone three drift")(tree);
    const m = marks(tree);
    expect(m).toHaveLength(1);
    expect(m[0].children?.[0].value).toBe("zone three drift");
  });

  it("only highlights the first occurrence", () => {
    const tree = paragraph("drift here and drift there");
    rehypeHighlightSnippet("drift")(tree);
    expect(marks(tree)).toHaveLength(1);
  });

  it("does nothing when the snippet isn't present or is blank", () => {
    const tree = paragraph("nothing to see");
    rehypeHighlightSnippet("absent")(tree);
    expect(marks(tree)).toHaveLength(0);

    const tree2 = paragraph("text");
    rehypeHighlightSnippet("   ")(tree2);
    expect(marks(tree2)).toHaveLength(0);
  });
});
