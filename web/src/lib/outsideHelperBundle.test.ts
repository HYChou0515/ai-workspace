/**
 * The outside helper turns a page into Markdown with THIS code, not a copy of
 * it (docs/plan-outside-helper.md D11, §3.4): `outsideHelperBundle.ts` is built
 * into the helper's package and run in its Chrome. Each sample page's Markdown
 * is pinned here, from the card's own converter; the helper's tests run the
 * built bundle in Chrome over the same samples and must match these files
 * byte for byte (tests/outside_helper/test_errands_in_chrome.py). A change to the
 * converter fails here first — `vitest -u` rewrites the files, and the
 * helper's parity test then checks the rebuilt bundle agrees.
 */
// @vitest-environment happy-dom
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { htmlToMarkdown } from "./htmlToMarkdown";
import { outsideHelper } from "./outsideHelperBundle";

const SAMPLES = resolve(__dirname, "../../../outside-helper/parity");
const pages = readdirSync(SAMPLES).filter((f) => f.endsWith(".html"));

describe("the outside helper's converter", () => {
  it("has sample pages to compare", () => {
    expect(pages.length).toBeGreaterThan(0);
  });

  it("is the card's own converter", () => {
    expect(outsideHelper.htmlToMarkdown).toBe(htmlToMarkdown);
  });

  for (const page of pages) {
    it(`pins ${page}`, async () => {
      const html = readFileSync(resolve(SAMPLES, page), "utf8");
      await expect(htmlToMarkdown(html) + "\n").toMatchFileSnapshot(
        resolve(SAMPLES, page.replace(/\.html$/, ".md")),
      );
    });
  }
});
