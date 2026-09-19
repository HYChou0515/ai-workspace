// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { AppSummary } from "../api/types";
import { NavGlyph } from "./NavGlyph";

afterEach(cleanup);

// The box is what lines every entry up whatever the glyph's own width — so it
// is the same for both forms, and every property of it is pinned here: the
// menus' own tests look at links and labels, not at this.
const BOX = {
  width: "22px",
  height: "22px",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  flexShrink: "0",
};

function app(over: Partial<AppSummary>): AppSummary {
  return { slug: "rca", title: "RCA", description: "", icon: "flame", color: "#F0502E", ...over };
}

/** Icon paints its colour on the paths inside the svg, not on the svg. */
function strokeOf(svg: Element | null): string | null {
  return svg?.querySelector("[stroke]")?.getAttribute("stroke") ?? null;
}

describe("NavGlyph", () => {
  it("draws a platform destination's icon, 16px, in the muted colour, inside the 22px box", () => {
    const { container } = render(<NavGlyph icon="layers" />);
    const svg = container.querySelector('[data-icon="layers"]');
    expect(svg).toBeInTheDocument();
    expect(svg!.parentElement).toHaveStyle(BOX);
    expect(svg).toHaveAttribute("width", "16");
    expect(strokeOf(svg)).toBe("var(--text-paper-d)");
  });

  it("forwards an App's manifest icon to AppIcon — a named key, in the App's colour, in the same box", () => {
    const { container } = render(<NavGlyph app={app({ icon: "flame" })} />);
    const svg = container.querySelector('[data-icon="flame"]');
    expect(svg).toBeInTheDocument();
    expect(svg).toHaveAttribute("width", "22");
    expect(strokeOf(svg)).toBe("#F0502E");
    expect(svg!.parentElement).toHaveStyle(BOX);
  });

  it("forwards an App's manifest icon to AppIcon — a shipped file, fetched per App", () => {
    // `slug` must reach AppIcon, or a PNG icon draws the fallback glyph.
    const { container } = render(<NavGlyph app={app({ slug: "yield", icon: "icon.png" })} />);
    const img = container.querySelector("img");
    expect(img).toHaveAttribute("src", "/api/apps/yield/icon");
    expect(img).toHaveAttribute("width", "22");
    expect(img!.parentElement).toHaveStyle(BOX);
  });

  it("boxes an emoji icon too, so its taller line box cannot push the row", () => {
    // AppIcon draws an emoji as a bare span at font-size 22; with the body
    // line-height that span is ~34px tall and threw a menu row out of line.
    const { container, getByText } = render(<NavGlyph app={app({ icon: "🔥" })} />);
    const span = getByText("🔥");
    expect(span.parentElement).toHaveStyle(BOX);
    expect(span.parentElement).toHaveStyle({ lineHeight: "1" });
    expect(container.firstElementChild).toBe(span.parentElement);
  });

  it("keeps the column for an App with no icon at all — the box is still 22px wide", () => {
    // The manifest defaults `icon` to ""; AppIcon renders that as an empty
    // span with no width, which pulled the label 22px left of every other row.
    const { container } = render(<NavGlyph app={app({ icon: "" })} />);
    expect(container.firstElementChild).toHaveStyle(BOX);
  });

  it("falls back to currentColor for an App with no colour — stroke='' would draw nothing", () => {
    const { container } = render(<NavGlyph app={app({ color: "" })} />);
    expect(strokeOf(container.querySelector('[data-icon="flame"]'))).toBe("currentColor");
  });
});
