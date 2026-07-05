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
  it.each<IconName>(["kanban", "panel_left", "split", "flame", "sparkle", "chat", "workflow", "wiki"])(
    "renders a non-empty glyph for %s",
    (name) => {
      const { container } = render(<Icon name={name} />);
      const svg = container.querySelector("svg");
      expect(svg).toBeInTheDocument();
      expect(svg!.childElementCount).toBeGreaterThan(0);
    },
  );

  it("tags the svg with its icon name so a glyph choice is identifiable (#466)", () => {
    const { container } = render(<Icon name="layers" />);
    expect(container.querySelector("svg")).toHaveAttribute("data-icon", "layers");
  });

  it("draws workflows with a glyph distinct from collections' layers (#466)", () => {
    // Workflows had reused the `layers` glyph = the collections identity; they
    // must now read as their own thing.
    const wf = render(<Icon name="workflow" />).container.querySelector("svg")?.innerHTML;
    const layers = render(<Icon name="layers" />).container.querySelector("svg")?.innerHTML;
    expect(wf).toBeTruthy();
    expect(wf).not.toBe(layers);
  });

  it("registers a real wiki glyph, distinct from layers and from the fallback tile (#466)", () => {
    // The wiki also reused `layers`; give it its own book glyph. Assert membership
    // (else `wiki` silently renders the unknown-key fallback) + that it's a real,
    // distinct glyph — not the fallback and not the collections `layers`.
    expect(isIconName("wiki")).toBe(true);
    const wiki = render(<Icon name="wiki" />).container.querySelector("svg")?.innerHTML;
    const layers = render(<Icon name="layers" />).container.querySelector("svg")?.innerHTML;
    const fallback = render(<Icon name={"totally-unknown" as IconName} />).container.querySelector(
      "svg",
    )?.innerHTML;
    expect(wiki).toBeTruthy();
    expect(wiki).not.toBe(layers);
    expect(wiki).not.toBe(fallback);
  });
});
