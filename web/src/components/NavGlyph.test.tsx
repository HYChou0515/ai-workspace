// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { NavGlyph } from "./NavGlyph";

afterEach(cleanup);

describe("NavGlyph", () => {
  it("draws a platform destination's icon inside the 22px box the switcher uses", () => {
    const { container } = render(<NavGlyph icon="layers" />);
    const svg = container.querySelector('[data-icon="layers"]');
    expect(svg).toBeInTheDocument();
    // The box is what lines every entry up whatever the glyph's own width.
    const box = svg!.parentElement!;
    expect(box).toHaveStyle({ width: "22px", height: "22px" });
    expect(svg).toHaveAttribute("width", "16");
  });

  it("forwards an App's manifest icon to AppIcon — a named key", () => {
    const { container } = render(
      <NavGlyph app={{ slug: "rca", title: "RCA", description: "", icon: "flame", color: "#F0502E" }} />,
    );
    const svg = container.querySelector('[data-icon="flame"]');
    expect(svg).toBeInTheDocument();
    expect(svg).toHaveAttribute("width", "22");
  });

  it("forwards an App's manifest icon to AppIcon — a shipped file, fetched per App", () => {
    // `slug` must reach AppIcon, or a PNG icon draws the fallback glyph.
    const { container } = render(
      <NavGlyph app={{ slug: "yield", title: "Yield", description: "", icon: "icon.png", color: "#2D6CC9" }} />,
    );
    const img = container.querySelector("img");
    expect(img).toHaveAttribute("src", "/api/apps/yield/icon");
    expect(img).toHaveAttribute("width", "22");
  });
});
