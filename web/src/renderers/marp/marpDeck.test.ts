import { describe, expect, it } from "vitest";

import { isMarpDoc, rewriteMarpAssets, slideScale } from "./marpDeck";

describe("isMarpDoc", () => {
  it("is true when the leading frontmatter sets marp: true", () => {
    expect(isMarpDoc("---\nmarp: true\ntheme: gaia\n---\n\n# Slide 1")).toBe(true);
  });

  it("is false for a plain markdown file with no frontmatter", () => {
    expect(isMarpDoc("# Just a heading\n\nsome prose")).toBe(false);
  });

  it("is false when frontmatter explicitly disables marp", () => {
    expect(isMarpDoc("---\nmarp: false\n---\n\n# Slide")).toBe(false);
  });

  it("is false when marp is absent from an otherwise present frontmatter", () => {
    expect(isMarpDoc("---\ntitle: Notes\ntheme: gaia\n---\n\n# Heading")).toBe(false);
  });

  it("requires the frontmatter to open at the very start of the file", () => {
    // A stray line before the fence means it is not Marp frontmatter.
    expect(isMarpDoc("intro\n---\nmarp: true\n---\n")).toBe(false);
  });

  it("tolerates CRLF line endings", () => {
    expect(isMarpDoc("---\r\nmarp: true\r\n---\r\n\r\n# Slide")).toBe(true);
  });
});

describe("rewriteMarpAssets", () => {
  const resolve = (src: string) => `/api/files?p=${src}`;

  it("rewrites a workspace-relative <img> src through the resolver", () => {
    const { html } = rewriteMarpAssets(`<img src="./diagram.png" alt="d">`, "", resolve);
    expect(html).toContain(`src="/api/files?p=./diagram.png"`);
  });

  it("leaves external and data/emoji image URLs untouched", () => {
    const html = `<img src="https://twemoji.example/1f44b.svg" class="emoji"><img src="data:image/png;base64,AAAA">`;
    expect(rewriteMarpAssets(html, "", resolve).html).toBe(html);
  });

  it("rewrites a workspace-relative CSS background-image url()", () => {
    const css = `section { background-image: url('./bg.jpg'); }`;
    expect(rewriteMarpAssets("", css, resolve).css).toContain(`url("/api/files?p=./bg.jpg")`);
  });

  it("rewrites a background url() inside an inline style attribute in the html", () => {
    const html = `<section style="background-image:url(./cover.png)"></section>`;
    expect(rewriteMarpAssets(html, "", resolve).html).toContain(`url("/api/files?p=./cover.png")`);
  });

  it("leaves an external CSS url() untouched", () => {
    const css = `.emoji{background:url(https://cdn.example/e.png)}`;
    expect(rewriteMarpAssets("", css, resolve).css).toBe(css);
  });
});

describe("slideScale", () => {
  it("scales a native 1280px slide to fit the pane width", () => {
    expect(slideScale(640)).toBeCloseTo(0.5);
    expect(slideScale(1280)).toBe(1);
  });

  it("accepts a custom native slide width", () => {
    expect(slideScale(600, 1200)).toBe(0.5);
  });

  it("upscales to fill a pane wider than the native slide", () => {
    expect(slideScale(2560)).toBe(2);
  });
});
