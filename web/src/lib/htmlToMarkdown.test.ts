// @vitest-environment happy-dom
/** Pasting a web page into the "請幫我查" card (docs/plan-outside-lookup.md D4). */
import { describe, expect, it } from "vitest";

import { htmlToMarkdown } from "./htmlToMarkdown";

describe("htmlToMarkdown", () => {
  it("keeps links", () => {
    expect(htmlToMarkdown('<p>See <a href="https://a.example/x">the notes</a>.</p>')).toBe(
      "See [the notes](https://a.example/x).",
    );
  });

  it("keeps a table as a table", () => {
    const md = htmlToMarkdown(
      "<table><thead><tr><th>版本</th><th>變更</th></tr></thead>" +
        "<tbody><tr><td>2.0</td><td>Arrow</td></tr></tbody></table>",
    );

    expect(md).toContain("| 版本 | 變更 |");
    expect(md).toContain("| 2.0 | Arrow |");
  });

  it("keeps headings, lists and code", () => {
    const md = htmlToMarkdown("<h2>Install</h2><ul><li>one</li><li>two</li></ul><pre><code>pip install x</code></pre>");

    expect(md).toContain("## Install");
    expect(md).toMatch(/-\s+one/);
    expect(md).toContain("pip install x");
  });

  it("drops what a page runs, not only what it shows", () => {
    const md = htmlToMarkdown(
      '<p onclick="steal()">ok</p><script>steal()</script><style>p{}</style><img src=x onerror="steal()">',
    );

    expect(md).toContain("ok");
    expect(md).not.toContain("steal");
    expect(md).not.toContain("p{}");
  });

  it("drops a javascript: link's target", () => {
    expect(htmlToMarkdown('<a href="javascript:steal()">x</a>')).not.toContain("steal");
  });
});
