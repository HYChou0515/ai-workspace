import { describe, expect, it } from "vitest";

import { remarkKbCitation } from "./remarkKbCitation";

/**
 * The plugin walks the mdast tree in-place. We don't want to take a dep on
 * remark to compile real markdown — we build tiny mdast trees by hand and
 * snapshot the splits. The shapes used here mirror what `remark-parse`
 * emits: paragraph > text, link > text-children.
 */
type N = { type: string; value?: string; url?: string; children?: N[] };

function run(tree: N): N {
  remarkKbCitation()(tree);
  return tree;
}

describe("remarkKbCitation", () => {
  it("leaves text without [N] markers alone", () => {
    const tree = run({
      type: "root",
      children: [{ type: "paragraph", children: [{ type: "text", value: "hello world" }] }],
    });
    expect(tree.children?.[0]?.children).toEqual([{ type: "text", value: "hello world" }]);
  });

  it("splits a [N] marker into a kb-cite link node", () => {
    const tree = run({
      type: "root",
      children: [
        { type: "paragraph", children: [{ type: "text", value: "see [12] for detail" }] },
      ],
    });
    const kids = tree.children?.[0]?.children ?? [];
    expect(kids).toHaveLength(3);
    expect(kids[0]).toEqual({ type: "text", value: "see " });
    expect(kids[1]?.type).toBe("link");
    expect(kids[1]?.url).toBe("kb-cite:12");
    expect(kids[1]?.children).toEqual([{ type: "text", value: "[12]" }]);
    expect(kids[2]).toEqual({ type: "text", value: " for detail" });
  });

  it("splits multiple markers in one text node", () => {
    const tree = run({
      type: "root",
      children: [
        { type: "paragraph", children: [{ type: "text", value: "[1] and also [2]." }] },
      ],
    });
    const kids = tree.children?.[0]?.children ?? [];
    const urls = kids.filter((n) => n.type === "link").map((n) => n.url);
    expect(urls).toEqual(["kb-cite:1", "kb-cite:2"]);
  });

  it("walks into nested children (strong, emphasis, etc.)", () => {
    const tree = run({
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [
            {
              type: "strong",
              children: [{ type: "text", value: "important [3]" }],
            },
          ],
        },
      ],
    });
    const strong = tree.children?.[0]?.children?.[0];
    const kids = strong?.children ?? [];
    expect(kids).toHaveLength(2);
    expect(kids[1]?.url).toBe("kb-cite:3");
  });

  it("skips code blocks so [N] inside code stays verbatim", () => {
    const tree = run({
      type: "root",
      children: [{ type: "code", value: "a=[1]" }],
    });
    const code = tree.children?.[0];
    // remark `code` nodes carry the literal text on `value` (no `children`),
    // so the walker leaves them untouched — `[1]` inside a fenced block must
    // not be split or it'd become a clickable button inside <pre>.
    expect(code?.value).toBe("a=[1]");
    expect(code?.children).toBeUndefined();
  });
});
