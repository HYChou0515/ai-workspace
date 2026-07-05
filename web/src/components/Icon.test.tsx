// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Icon, type IconName } from "./Icon";

describe("<Icon />", () => {
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
