// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { blockMaskedCopy } from "./maskedSecret";

afterEach(cleanup);

function field(masked: boolean) {
  render(
    <input
      data-testid="secret"
      className="input"
      data-masked={masked}
      defaultValue="s3cret"
      autoComplete="off"
      spellCheck={false}
      autoCapitalize="off"
      autoCorrect="off"
      onCopy={blockMaskedCopy}
      onCut={blockMaskedCopy}
      onDragStart={blockMaskedCopy}
    />,
  );
  return screen.getByTestId("secret");
}

describe("blockMaskedCopy", () => {
  // A password field refuses copy and cut; a masked text field does not unless
  // told — dots on screen, the plain value on the clipboard.
  it("refuses copy and cut while the value is drawn as dots", () => {
    const el = field(true);
    // fireEvent returns false when a handler called preventDefault.
    expect(fireEvent.copy(el)).toBe(false);
    expect(fireEvent.cut(el)).toBe(false);
    // Dragging the selected dots into another field drops the plain value
    // (review round 2); a password field does not let its text be dragged.
    expect(fireEvent.dragStart(el)).toBe(false);
  });

  it("lets the value go once it is shown", () => {
    const el = field(false);
    expect(fireEvent.copy(el)).toBe(true);
    expect(fireEvent.cut(el)).toBe(true);
    expect(fireEvent.dragStart(el)).toBe(true);
  });
});
