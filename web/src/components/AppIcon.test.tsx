// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AppIcon } from "./AppIcon";

afterEach(cleanup);

describe("AppIcon", () => {
  it("renders inline svg markup as-is", () => {
    const { container } = render(<AppIcon icon='<svg data-testid="m"></svg>' />);
    expect(container.querySelector("svg")).toBeInTheDocument();
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
});
