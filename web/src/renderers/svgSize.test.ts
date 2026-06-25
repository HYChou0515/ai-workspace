import { describe, expect, it } from "vitest";

import { svgNaturalSize } from "./svgSize";

describe("svgNaturalSize", () => {
  it("reads the intrinsic size from a viewBox-only SVG (no width/height)", () => {
    // What mermaid / draw.io emit: a viewBox but no width/height. The browser's
    // <img> reports a tiny 300x150-derived default here, so we must parse the
    // viewBox to recover the real aspect/size.
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 700"></svg>`;
    expect(svgNaturalSize(svg)).toEqual({ w: 1000, h: 700 });
  });

  it("falls back to absolute width/height when there is no viewBox", () => {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600"></svg>`;
    expect(svgNaturalSize(svg)).toEqual({ w: 800, h: 600 });
  });

  it("reads width/height given in px units", () => {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="320px" height="240px"></svg>`;
    expect(svgNaturalSize(svg)).toEqual({ w: 320, h: 240 });
  });

  it("prefers the viewBox over a tiny presentational width/height", () => {
    // Some exporters set a small display size but a large coordinate viewBox.
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="28" viewBox="0 0 1000 700"></svg>`;
    expect(svgNaturalSize(svg)).toEqual({ w: 1000, h: 700 });
  });

  it("returns null for percentage sizes with no viewBox (defer to the <img>)", () => {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%"></svg>`;
    expect(svgNaturalSize(svg)).toBeNull();
  });

  it("returns null when there is no usable size and never throws", () => {
    expect(svgNaturalSize(`<svg xmlns="http://www.w3.org/2000/svg"></svg>`)).toBeNull();
    expect(svgNaturalSize("not even svg <<<")).toBeNull();
  });

  it("returns null for degenerate zero dimensions", () => {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" viewBox="0 0 0 0"></svg>`;
    expect(svgNaturalSize(svg)).toBeNull();
  });
});
