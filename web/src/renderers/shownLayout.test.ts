/**
 * `show_file(layout=…)` (#847 PR 3 P4): the declaration carries the pane tree
 * beside the flat file list, and the chat draws ONE card for it.
 */
import { describe, expect, it } from "vitest";

import { parseShownLayout } from "./shownFiles";

const png = (path: string) => ({ path, mime: "image/png", size: 10 });
const tree = {
  type: "split",
  dir: "row",
  ratio: 0.4,
  a: { type: "leaf", path: "/a.ai.yaml" },
  b: { type: "leaf", path: "/b.png" },
};
const declare = (body: unknown) => `shown.\n[shown-files]${JSON.stringify(body)}`;

describe("parseShownLayout", () => {
  it("reads the tree, the caption and the files", () => {
    const out = parseShownLayout(
      declare({ shown_files: [png("/a.ai.yaml"), png("/b.png")], layout: tree, caption: "linked" }),
    );
    expect(out).toEqual({
      layout: tree,
      files: [png("/a.ai.yaml"), png("/b.png")],
      caption: "linked",
    });
  });

  it("is null for an ordinary declaration", () => {
    expect(parseShownLayout(declare({ shown_files: [png("/b.png")] }))).toBeNull();
    expect(parseShownLayout("no declaration")).toBeNull();
    expect(parseShownLayout(undefined)).toBeNull();
  });

  it("is null for a truncated (still streaming) declaration", () => {
    const whole = declare({ shown_files: [png("/a.ai.yaml"), png("/b.png")], layout: tree });
    expect(parseShownLayout(whole.slice(0, -5))).toBeNull();
  });

  it("is null when the tree is malformed, so the files render one by one", () => {
    const bad = [
      { ...tree, dir: "diagonal" },
      { ...tree, ratio: 2 },
      { ...tree, b: undefined },
      { ...tree, a: { type: "leaf" } },
      { type: "leaf", path: "/b.png" },
    ];
    for (const layout of bad) {
      expect(
        parseShownLayout(declare({ shown_files: [png("/a.ai.yaml"), png("/b.png")], layout })),
      ).toBeNull();
    }
  });

  it("is null when a leaf names a file the declaration did not describe", () => {
    // The backend describes every leaf; a leaf without an entry has no mime or
    // size to draw, and is not a file the backend saw.
    expect(parseShownLayout(declare({ shown_files: [png("/b.png")], layout: tree }))).toBeNull();
  });
});
