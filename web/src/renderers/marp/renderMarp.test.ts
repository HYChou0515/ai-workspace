import { describe, expect, it } from "vitest";

import { renderMarp } from "./renderMarp";

// Exercises the REAL @marp-team/marp-core engine (render is a pure string
// transform, no browser DOM needed) so a broken/incompatible bundle is caught
// here, not only in the manual web-demo.
describe("renderMarp (real @marp-team/marp-core)", () => {
  it("renders a two-slide deck into <section> blocks plus theme css", () => {
    const { html, css } = renderMarp("---\nmarp: true\ntheme: default\n---\n\n# Hello\n\n---\n\n# World");
    expect((html.match(/<section/g) ?? []).length).toBe(2);
    expect(css).toContain("section");
  });

  it("emits no <script> tag when script is disabled", () => {
    const { html } = renderMarp("---\nmarp: true\n---\n\n# Slide");
    expect(html).not.toContain("<script");
  });
});
