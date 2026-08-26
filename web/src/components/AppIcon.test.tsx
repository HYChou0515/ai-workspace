// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AppIcon } from "./AppIcon";

afterEach(cleanup);

describe("AppIcon", () => {
  it("renders a file icon as an <img> pointing at the App's icon route", () => {
    // The manifest names the FILE; the picture itself is fetched. That is what
    // lets an App ship a PNG — a raster has no markup to inline.
    const { container } = render(<AppIcon icon="icon.png" slug="rca" />);
    const img = container.querySelector("img");
    expect(img).toBeInTheDocument();
    expect(img).toHaveAttribute("src", "/api/apps/rca/icon");
  });

  it("renders a short non-name grapheme as an emoji", () => {
    const { getByText } = render(<AppIcon icon="🔥" />);
    expect(getByText("🔥")).toBeInTheDocument();
  });

  it("renders a named-icon key via the Icon set (no emoji span, no raw svg string)", () => {
    const { container } = render(<AppIcon icon="flame" color="#F0502E" />);
    // named icons resolve through the Icon component, not the emoji/svg-string paths
    expect(container.textContent).toBe("");
  });

  it("renders a fallback glyph for an unknown icon key — never an empty tile (#456)", () => {
    // A manifest icon key that isn't in the icon set (e.g. pm's "kanban" once was)
    // must still draw something, not a hollow <svg>.
    const { container } = render(<AppIcon icon="mysteryicon" />);
    const svg = container.querySelector("svg");
    expect(svg).toBeInTheDocument();
    expect(svg?.querySelector("path, rect, circle")).toBeInTheDocument();
  });
});
