// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Icon, type IconName, isIconName } from "./Icon";

afterEach(cleanup);

describe("Icon", () => {
  it("recognizes kanban as a registered icon so the PM app renders a real glyph (#456)", () => {
    expect(isIconName("kanban")).toBe(true);
  });

  it("rejects an unregistered key", () => {
    expect(isIconName("mysteryicon")).toBe(false);
  });

  it("draws the kanban glyph as multiple columns (a board, not the neutral fallback tile)", () => {
    const { container } = render(<Icon name="kanban" />);
    const shapes = container.querySelectorAll("svg > *");
    // a board has more than one column/shape; the unknown-key fallback is a single rect
    expect(shapes.length).toBeGreaterThan(1);
  });

  // A named icon with no entry in `paths` renders an EMPTY <svg> (invisible) —
  // that's exactly how the PM app's "kanban" icon went missing. Guard the glyphs
  // that ship as app/UI identity so a typo or a dropped entry fails loudly here.
  it.each<IconName>(["kanban", "panel_left", "split", "flame", "sparkle", "chat"])(
    "renders a non-empty glyph for %s",
    (name) => {
      const { container } = render(<Icon name={name} />);
      const svg = container.querySelector("svg");
      expect(svg).toBeInTheDocument();
      expect(svg!.childElementCount).toBeGreaterThan(0);
    },
  );
});
