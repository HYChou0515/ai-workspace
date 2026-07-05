// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Icon, isIconName } from "./Icon";

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
});
